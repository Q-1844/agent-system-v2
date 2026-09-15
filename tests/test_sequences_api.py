# tests/test_sequences_api.py
# Sequence S1 — 最小 REST API 合同测试（24.md §25）。
# TestClient 不真调 ffmpeg：render dispatch 用 stub 队列验证合同（真实 render 在
# service 单测 + 真机 E2E 覆盖）。
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from deps import JOB_HANDLERS, projects_dir
from src.core.models import Project, Scene, Shot, ShotStatus, VideoVersion
from src.core.store import ProjectStore

PREFIX = "_test_seq_api_"


def _dir(name: str) -> Path:
    return Path(projects_dir) / name


@pytest.fixture()
def seq_project(client):
    """建测试项目 + 两个带 done VideoVersion 的镜头（final 指针在位）。"""
    name = PREFIX + "base"
    _cleanup([name])
    r = client.post("/api/projects/create", params={"name": name,
                                                    "render_family": "2d_animation"})
    assert r.status_code == 200, r.text
    vdir = _dir(name) / "output" / "videos"
    vdir.mkdir(parents=True, exist_ok=True)
    files = {}
    for vid, fn in (("vv_a1", "s01_c01.mp4"), ("vv_b1", "s01_c02.mp4")):
        f = vdir / fn
        f.write_bytes(b"fake-mp4")
        files[vid] = str(f)
    store = ProjectStore(_dir(name))
    store.save(Project(name=name, scenes=[Scene(scene_id="s01", shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             video_versions=[VideoVersion(version_id="vv_a1", local=files["vv_a1"],
                                          status=ShotStatus.DONE.value)],
             final_video="vv_a1"),
        Shot(shot_id="s01_c02", scene_id="s01", index=2,
             video_versions=[VideoVersion(version_id="vv_b1", local=files["vv_b1"],
                                          status=ShotStatus.DONE.value)],
             final_video="vv_b1"),
    ])]))
    yield client, name
    _cleanup([name])


def _cleanup(names):
    for n in names:
        d = _dir(n)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


class _StubQueue:
    def __init__(self):
        self.submitted = []

    def submit(self, project, kind, shot_id=None, **params):
        self.submitted.append({"project": project, "kind": kind,
                               "shot_id": shot_id, "params": params})
        return SimpleNamespace(job_id="stubjob_001")


# ============ CRUD ============

def test_sequence_crud_roundtrip(seq_project):
    client, name = seq_project
    r = client.post(f"/api/project/{name}/sequences", json={
        "title": "开场", "scene_id": "s01",
        "clips": [{"shot_id": "s01_c01"}, {"shot_id": "s01_c02",
                                           "trim_in_ms": 200, "trim_out_ms": 100}]})
    assert r.status_code == 200, r.text
    body = r.json()
    sid, rev = body["sequence_id"], body["revision"]
    assert sid.startswith("seq_") and len(rev) == 8
    assert body["block"]["clips"][1]["trim_in_ms"] == 200

    r = client.get(f"/api/project/{name}/sequences")
    assert r.status_code == 200
    assert any(s["sequence_id"] == sid for s in r.json()["sequences"])

    r = client.get(f"/api/project/{name}/sequences/{sid}")
    assert r.status_code == 200
    detail = r.json()["sequence"]
    assert detail["revision"] == rev and detail["final_render"] is None

    # 更新：顺序反转 → revision 变；display-only 不变
    r = client.put(f"/api/project/{name}/sequences/{sid}", json={
        "clips": [{"shot_id": "s01_c02", "trim_in_ms": 200, "trim_out_ms": 100},
                  {"shot_id": "s01_c01"}]})
    assert r.status_code == 200
    assert r.json()["revision"] != rev

    # OCC：旧 revision 冲突 → 409
    r = client.put(f"/api/project/{name}/sequences/{sid}", json={
        "title": "x", "expected_revision": rev})
    assert r.status_code == 409

    # 删除 → 404
    assert client.delete(f"/api/project/{name}/sequences/{sid}").status_code == 200
    assert client.get(f"/api/project/{name}/sequences/{sid}").status_code == 404


def test_sequence_validation_400(seq_project):
    client, name = seq_project
    r = client.post(f"/api/project/{name}/sequences", json={
        "title": "空", "clips": []})
    assert r.status_code == 400
    r = client.post(f"/api/project/{name}/sequences", json={
        "title": "负 trim", "clips": [{"shot_id": "s01_c01", "trim_in_ms": -1}]})
    assert r.status_code == 400


def test_sequence_404s(seq_project):
    client, name = seq_project
    assert client.get(f"/api/project/{name}/sequences/seq_nope").status_code == 404
    assert client.get("/api/project/_no_such_proj_/sequences").status_code == 404


# ============ render dispatch 合同（§10/§22/§25）============

def test_render_dispatch_creates_queued_row(seq_project, monkeypatch):
    client, name = seq_project
    r = client.post(f"/api/project/{name}/sequences", json={
        "title": "r", "clips": [{"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}]})
    sid = r.json()["sequence_id"]

    stub = _StubQueue()
    monkeypatch.setattr("deps.job_queue", stub)
    r = client.post(f"/api/project/{name}/sequences/{sid}/render")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == "stubjob_001"
    assert body["render_version_id"].startswith("srender_")
    assert body["status"] == "queued"
    assert stub.submitted and stub.submitted[0]["kind"] == "sequence_render"
    assert stub.submitted[0]["params"] == {"sequence_id": sid,
                                           "render_version_id": body["render_version_id"]}

    # queued 行在列；§10：未 select；freshness missing
    r = client.get(f"/api/project/{name}/sequences/{sid}/renders")
    rows = r.json()["render_versions"]
    assert len(rows) == 1 and rows[0]["status"] == "queued"
    assert client.get(f"/api/project/{name}/sequences/{sid}/freshness"
                      ).json()["status"] == "missing"

    # §10：queued 不可 select
    r = client.post(f"/api/project/{name}/sequences/{sid}/renders/select",
                    json={"version_id": body["render_version_id"]})
    assert r.status_code == 400


def test_sequence_render_handler_registered():
    """handler 在模块加载时注册（compose 同先例），startup 前即可提交。"""
    assert JOB_HANDLERS.get("sequence_render") is not None


def test_audio_plan_get_put_occ(seq_project):
    """25.md §37/§38：AudioPlan GET/PUT + OCC 409；空 layers = 清空（S1 语义恢复）。"""
    client, name = seq_project
    r = client.post(f"/api/project/{name}/sequences", json={
        "title": "ap", "clips": [{"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}]})
    sid = r.json()["sequence_id"]
    # 初始无 plan
    r = client.get(f"/api/project/{name}/sequences/{sid}/audio-plan")
    assert r.status_code == 200 and r.json()["audio_plan"] is None
    # PUT 合法 plan
    layer = {"role": "music",
             "source": {"kind": "imported_audio", "file": "audio_imports/m.wav"},
             "start_ms": 0, "duration_ms": 14000, "gain_db": -18.0,
             "fade_in_ms": 2000, "fade_out_ms": 3000}
    r = client.put(f"/api/project/{name}/sequences/{sid}/audio-plan",
                   json={"layers": [layer]})
    assert r.status_code == 200, r.text
    rev = r.json()["revision"]
    assert r.json()["audio_plan"]["layers"][0]["gain_db"] == -18.0
    # GET 回读一致
    r = client.get(f"/api/project/{name}/sequences/{sid}/audio-plan")
    assert r.json()["revision"] == rev
    # §38 OCC：错 revision → 409
    r = client.put(f"/api/project/{name}/sequences/{sid}/audio-plan",
                   json={"layers": [layer | {"gain_db": -8.0}], "expected_revision": rev})
    assert r.status_code == 200
    r = client.put(f"/api/project/{name}/sequences/{sid}/audio-plan",
                   json={"layers": [layer], "expected_revision": rev})
    assert r.status_code == 409
    # §8：非法 role → 400；非法 source kind → 400
    r = client.put(f"/api/project/{name}/sequences/{sid}/audio-plan",
                   json={"layers": [layer | {"role": "sfx"}]})
    assert r.status_code == 400
    r = client.put(f"/api/project/{name}/sequences/{sid}/audio-plan",
                   json={"layers": [layer | {"source": {"kind": "mystery"}}]})
    assert r.status_code == 400
    # 清空 → None（S1 语义恢复）
    r = client.put(f"/api/project/{name}/sequences/{sid}/audio-plan", json={"layers": []})
    assert r.status_code == 200 and r.json()["audio_plan"] is None
    # No Auto Render：PUT 后无任何 render 行产生
    r = client.get(f"/api/project/{name}/sequences/{sid}/renders")
    assert r.json()["render_versions"] == []


def test_freshness_endpoint_contract(seq_project):
    """§20/§21 读模型经 API 外显：missing →（服务层造 render）untracked_legacy。"""
    client, name = seq_project
    r = client.post(f"/api/project/{name}/sequences", json={
        "title": "f", "clips": [{"shot_id": "s01_c01"}]})
    sid = r.json()["sequence_id"]
    assert client.get(f"/api/project/{name}/sequences/{sid}/freshness"
                      ).json()["status"] == "missing"
    # 直接落一条无 basis 的 done render（模拟 legacy）——untracked_legacy 诚实暴露
    from src.sequence.service import SequenceService
    from src.sequence.models import SequenceRenderVersion, RSTATUS_DONE
    svc = SequenceService(_dir(name), name)
    svc.create_render(sid)
    entries = svc.store.load()          # 单线程测试：直接 store 读写即可（无并发）
    entries[sid].render_versions[0].status = RSTATUS_DONE
    svc.store.save(entries)
    vid = entries[sid].render_versions[0].version_id
    # final_render 未设 → 默认仍 missing；显式 ?version_id= 查该版本 → untracked_legacy
    r = client.get(f"/api/project/{name}/sequences/{sid}/freshness")
    assert r.status_code == 200 and r.json()["status"] == "missing"
    r = client.get(f"/api/project/{name}/sequences/{sid}/freshness?version_id={vid}")
    assert r.status_code == 200
    assert r.json()["status"] == "untracked_legacy"
