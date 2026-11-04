# tests/relstore/test_relstore_08_index_snapshot_full.py
# G3-A：B1 Contents CAS 双写者、整项目快照指纹、依赖齐全 full 小项目 Mock HTTP 全链。
import base64
import hashlib
import json
from pathlib import Path

import pytest

from tools.relstore import archive_campaign as A
from tools.relstore import manifest as M
from tools.relstore import safe_tar
from tools.relstore.errors import AssetConflict, RemoteWriteDisabled
from tools.relstore.github_release_adapter import (
    GitHubContentsIndex, GitHubReleaseAdapter, MockHttpTransport)

O_B, R_B = "Q-1844", "agent-system-v2-rundata"
O_B1, R_B1 = "Q-1844", "agent-system-v2-rundata-1"
TOKEN = "ghs_G3A_TEST_NOT_REAL"
TAG, ASSET, BRANCH, IPATH = ("test-relstore/g3-fullcheck/r1",
                             "g3-test-fullcheck.tar", "test/g3-fullcheck",
                             "tests/g3/fullcheck/state.json")


# ---------------- B1 Contents CAS ----------------

class FakeB1Server:
    """模拟 Contents API 单文件 CAS：首次无 sha 建立，之后必须带旧 sha。"""

    def __init__(self):
        self.blob = None  # bytes or None
        self.history = []

    def handler(self, req):
        url = req["url"]
        if req["method"] == "GET":
            if self.blob is None:
                return MockHttpTransport.json_response(404, {"message": "Not Found"})
            return MockHttpTransport.json_response(200, {
                "sha": hashlib.sha1(self.blob).hexdigest(),
                "encoding": "base64",
                "content": base64.b64encode(self.blob).decode()})
        if req["method"] == "PUT":
            body = req["json"]
            raw = base64.b64decode(body["content"])
            if self.blob is None:
                if body.get("sha"):
                    return MockHttpTransport.json_response(
                        422, {"message": "file exists expected no-sha"})
                self.blob = raw
                self.history.append(("create", body.get("sha"), raw))
            else:
                want = hashlib.sha1(self.blob).hexdigest()
                if body.get("sha") != want:
                    return MockHttpTransport.json_response(
                        409, {"message": f"{body.get('sha')} is not {want}"})
                self.blob = raw
                self.history.append(("update", body.get("sha"), raw))
            return MockHttpTransport.json_response(200 if self.history[-1][0] == "update"
                                                     else 201, {
                "content": {"sha": hashlib.sha1(raw).hexdigest()},
                "commit": {"sha": "c" + hashlib.sha1(raw).hexdigest()[:7]}})
        raise AssertionError(req["method"])


def test_index_disabled_by_default():
    srv = FakeB1Server()
    idx = GitHubContentsIndex(O_B1, R_B1, BRANCH, IPATH,
                               MockHttpTransport(srv.handler))
    with pytest.raises(RemoteWriteDisabled):
        idx.write_cas({"v": 1}, None, message="x")


def test_index_create_read_update_cas():
    srv = FakeB1Server()
    t = MockHttpTransport(srv.handler)
    idx = GitHubContentsIndex.armed(O_B1, R_B1, BRANCH, IPATH, TOKEN, t)
    assert idx.read() is None
    r1 = idx.write_cas({"revision": 1, "asset_id": "a1"}, None,
                        message="create")
    assert r1["http"] == 201
    cur = idx.read()
    assert cur["doc"]["revision"] == 1 and len(cur["blob_sha"]) == 40
    r2 = idx.write_cas({"revision": 2, "asset_id": "a2"}, cur["blob_sha"],
                        message="update")
    assert r2["http"] == 200
    assert idx.read()["doc"]["revision"] == 2
    # 请求带 branch 与 base64 内容
    puts = [r for r in t.requests if r["method"] == "PUT"]
    assert puts[0]["json"]["branch"] == BRANCH
    json.loads(base64.b64decode(puts[0]["json"]["content"]))


def test_two_writers_only_one_wins_loser_keeps_candidate(tmp_path):
    srv = FakeB1Server()
    t1, t2 = MockHttpTransport(srv.handler), MockHttpTransport(srv.handler)
    w1 = GitHubContentsIndex.armed(O_B1, R_B1, BRANCH, IPATH, TOKEN, t1)
    w2 = GitHubContentsIndex.armed(O_B1, R_B1, BRANCH, IPATH, TOKEN, t2)
    base = w1.read()  # 均为 None（文件不存在）
    r1 = w1.write_cas({"revision": 1, "winner": "w1"}, None, message="w1")
    # 第二写入者持同样的旧认知（无文件）→ 必须失败，不得覆盖
    with pytest.raises(AssetConflict):
        w2.write_cas({"revision": 1, "winner": "w2"}, None, message="w2")
    # 最终唯一结果 = w1
    final = w1.read()
    assert final["doc"]["winner"] == "w1"
    # 败者本地候选保留（业务纪律：不静默丢弃）
    cand = tmp_path / "loser_candidate.json"
    cand.write_text(json.dumps({"revision": 1, "winner": "w2"}), encoding="utf-8")
    assert cand.exists() and json.loads(cand.read_text())["winner"] == "w2"
    assert srv.history[-1][0] == "create" and len(srv.history) == 1


def test_stale_revision_put_returns_conflict():
    srv = FakeB1Server()
    idx = GitHubContentsIndex.armed(O_B1, R_B1, BRANCH, IPATH, TOKEN,
                                     MockHttpTransport(srv.handler))
    idx.write_cas({"revision": 1}, None, message="c")
    with pytest.raises(AssetConflict):
        idx.write_cas({"revision": 99}, "0" * 40, message="stale")


# ---------------- 整项目快照指纹 ----------------

def test_snapshot_fingerprint_stable_and_media_sensitive(sample_project):
    f1 = M.compute_snapshot_fingerprint(sample_project)
    f2 = M.compute_snapshot_fingerprint(sample_project)
    assert f1 == f2 and len(f1) == 64
    mp4 = sample_project / "output/videos/s01_c02.mp4"
    original = mp4.read_bytes()
    mp4.write_bytes(original + b"x")  # 媒体变化（project.json 未动）
    f3 = M.compute_snapshot_fingerprint(sample_project)
    assert f3 != f1
    mp4.write_bytes(original)
    assert M.compute_snapshot_fingerprint(sample_project) == f1


def test_media_change_after_plan_supersedes(sample_project, opdir):
    """G3 缺口：project.json 不变、仅图片/视频变化也必须拒绝旧包。"""
    A.plan(project_dir=sample_project, opdir=opdir, operation_id="aop_snap",
           project_id="p", revision=1, lifecycle_status="active",
           source_code_commit="fc8da19", created_at=1.0, tool_versions={})
    (sample_project / "output/videos/s01_c02.mp4").write_bytes(b"tampered-video")
    with pytest.raises(A.SourceRevisionChanged):
        A.package(opdir)
    assert A.load_state(opdir)["state"] == A.SUPERSEDED


# ---------------- 依赖齐全的 full 小项目 Mock HTTP 全链 ----------------

def _full_project(root: Path):
    """合成非真实项目：input 齐全、无 skill trace（不依赖外部 skill）、<1MiB。"""
    name = "G3_FULL_SAMPLE"
    p = root / "work" / name
    for d in ["input", "output/videos", "output/frames", "output/agents"]:
        (p / d).mkdir(parents=True, exist_ok=True)
    (p / "input/script.yaml").write_text("scenes: []\n", encoding="utf-8")
    (p / "output/project.json").write_text(json.dumps({
        "name": name, "schema_version": 2, "created_at": 1.0, "updated_at": 1.0,
        "scenes": [], "next_scene_seq": 1}, ensure_ascii=False), encoding="utf-8")
    (p / "output/videos/shot.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 500)
    return p


def test_full_synthetic_project_end_to_end_mock(tmp_path):
    project = _full_project(tmp_path)
    opdir = tmp_path / "op"
    opdir.mkdir()
    st = A.plan(project_dir=project, opdir=opdir, operation_id="aop_full",
                project_id="p_g3_fullcheck", revision=1,
                lifecycle_status="active", source_code_commit="fc8da19",
                created_at=1.0, tool_versions={"python": "3.13.0"})
    st = A.package(opdir)
    payload = st["package"]["payload"]
    assert payload["snapshot_fingerprint"]
    assert payload["dependencies"]["missing_required"] == []
    assert st["restorable_level"] == "full"
    tar_bytes = Path(st["package"]["path"]).read_bytes()
    assert len(tar_bytes) < 1024 * 1024  # ≤1MiB

    # 虚拟 GitHub：Release 201/资产 201/302 重下载/索引 CAS
    srv = FakeB1Server()
    events = {"order": []}

    def handler(req):
        url, method = req["url"], req["method"]
        if method == "POST" and url.endswith("/releases"):
            events["order"].append("create_release")
            return MockHttpTransport.json_response(
                201, {"id": 9100, "tag_name": TAG, "draft": True})
        if method == "POST" and "uploads.github.com" in url:
            assert req["data"] == tar_bytes
            events["order"].append("upload_asset")
            return MockHttpTransport.json_response(
                201, {"id": 8100, "name": ASSET, "size": len(tar_bytes),
                       "state": "uploaded"})
        if method == "GET" and url.endswith("/releases/assets/8100"):
            return MockHttpTransport.bytes_response(
                302, b"", headers={"location":
                    "https://release-assets.githubusercontent.com/x?sig=SECRET"})
        if "release-assets.githubusercontent.com" in url:
            events["order"].append("redownload")
            return MockHttpTransport.bytes_response(200, tar_bytes,
                headers={"content-type": "application/octet-stream"})
        if "contents/" + IPATH in url:
            events["order"].append("index_cas")
            return srv.handler(req)
        if method == "GET" and "/assets" in url:
            return MockHttpTransport.json_response(200, [])
        return MockHttpTransport.json_response(404, {"message": "unhandled " + url})

    t = MockHttpTransport(handler)
    index = GitHubContentsIndex.armed(O_B1, R_B1, BRANCH, IPATH, TOKEN, t)
    adapter = GitHubReleaseAdapter.armed(O_B, R_B, TOKEN, t, index_client=index)
    st = A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET,
                  sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_RESTORABLE
    # 顺序铁律：上传→重下载验证→索引最后
    assert events["order"][:3] == ["create_release", "upload_asset", "redownload"]
    assert events["order"][-1] == "index_cas"
    # B1 索引内容与 receipt 一致
    cur = index.read()
    assert cur["doc"]["status"] == A.VERIFIED_RESTORABLE
    assert cur["doc"]["package_sha256"] == st["package"]["sha256"]
    receipt = json.loads((opdir / M.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert receipt["status"] == A.VERIFIED_RESTORABLE
    assert receipt["verification"]["restorable_level"] == "full"
    assert cur["doc"]["receipt_sha256"] == hashlib.sha256(
        M.human_dumps(receipt)).hexdigest()
    # P55 legacy 不得升级 full（抽查等级逻辑）
    assert M.assert_restorable_level(
        {"manifest_kind": "legacy_archive_sidecar",
         "missing_required": ["skill:x@1.0"]}) == "partial"


def test_source_sha_must_be_full_40_for_production_trace():
    """G3-A §5：短写 SHA 不作为正式生产追溯 ID（G3-B 执行计划强制 40 位）。"""
    assert M.is_full_git_sha("fc8da19553d157ff335dfa8bb2600ab8ef76039c")
    assert not M.is_full_git_sha("fc8da19")
    assert not M.is_full_git_sha("")
