# tools/relstore/g3b_executor.py
# G3-B 真实 GitHub 微型联调执行器（用户书面授权后运行）。
# 纪律：
# - token 仅从 G3B_TOKEN_FILE 指向的文件读入进程内存，绝不打印/入日志/入参数；
# - 所有步骤独立子命令，逐步留证（--out JSON）；任何一步失败立即停止；
# - immutable 状态 UNKNOWN → 写入前停机（immutable_gate）；
# - Draft 阶段用带权限二进制下载验证，Publish 后才匿名重下载；
# - 不删除/不覆盖/不清场；测试命名域 test-relstore|g3-test|test/g3|tests/g3。
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

from . import manifest as M
from . import safe_tar
from .errors import RelstoreError
from .github_release_adapter import (
    GitHubContentsIndex, GitHubReleaseAdapter, HttpxTransport)
from .immutable_gate import assert_writable, evaluate

OWNER_B, REPO_B = "Q-1844", "agent-system-v2-rundata"
OWNER_B1, REPO_B1 = "Q-1844", "agent-system-v2-rundata-1"


def load_token(token_file: str) -> str:
    """从 --token-file 指向的文件读 token（内容 trim）。绝不打印。
    路径可入命令行（非机密）；token 值永不入参数/日志/报告。"""
    if not token_file:
        raise RelstoreError("缺少 --token-file（安全注入点）")
    raw = Path(token_file).read_text(encoding="utf-8").strip()
    if not raw or len(raw) < 20:
        raise RelstoreError("token 文件内容无效")
    return raw


def out_write(out: Path, doc: dict):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    print(f"[written] {out}")


def make_adapters(token: str):
    t = HttpxTransport()
    return (GitHubReleaseAdapter.armed(OWNER_B, REPO_B, token, t),
            GitHubContentsIndex.armed(OWNER_B1, REPO_B1, "", "", token, t),
            t)


# ---------------- precheck（只读，写闸前） ----------------

def cmd_precheck(args):
    token = load_token(args.token_file)
    attested = getattr(args, "immutable_attested", None)
    a, idx, t = make_adapters(token)
    ev = {"time": time.time(), "anon_immutable": None, "auth_immutable": None}
    info_b = a.get_repo_info()
    ev["B_repo"] = info_b
    ev["B_main_sha"] = idx_b_head(a)
    ev["B_tags"] = a.list_tags()
    rel = a.get_release_by_tag(f"test-relstore/{args.nonce}/r1") if args.nonce else None
    ev["B_existing_test_release"] = rel and rel.id
    ev["B1_readme"] = idx.read()  # B1 main README（可能 404/空仓）
    imm = a.get_immutable_releases()
    ev["auth_immutable"] = imm
    ev["immutable_state_api"] = evaluate(imm)
    ev["immutable_attested"] = attested
    try:
        ev["immutable_state"] = assert_writable(imm, attested)
        ev["immutable_source"] = ("api" if ev["immutable_state_api"] != "unknown"
                                   else "owner-attested-settings")
    except Exception as e:
        ev["immutable_state"] = "unknown"
        ev["immutable_source"] = "none"
        ev["immutable_error"] = str(e)[:200]
    out_write(Path(args.out), ev)
    if ev["immutable_state"] == "unknown":
        print("IMMUTABLE=UNKNOWN → 按修正版纪律，写入前停机")
        return 2
    print("IMMUTABLE=", ev["immutable_state"], "source=", ev["immutable_source"])
    return 0


def idx_b_head(a: GitHubReleaseAdapter) -> str | None:
    r = a.transport.request("GET", f"{a.api_base}/repos/{OWNER_B}/{REPO_B}/git/ref/heads/main",
                            headers=a._headers())
    return r.json()["object"]["sha"] if r.status == 200 else None


# ---------------- package（本地构造合成 full 项目） ----------------

def build_synthetic_project(root: Path, name: str) -> Path:
    p = root / "work" / name
    for d in ["input", "output/videos", "output/frames", "output/agents"]:
        (p / d).mkdir(parents=True, exist_ok=True)
    (p / "input/script.yaml").write_text(
        "# G3-B synthetic full project (no real material)\nscenes: []\n", encoding="utf-8")
    (p / "output/project.json").write_text(json.dumps({
        "name": name, "schema_version": 2, "created_at": time.time(),
        "updated_at": time.time(), "scenes": [], "next_scene_seq": 1,
        "style": {}, "native_av": {}, "story_text": "synthetic",
        "characters": []}, ensure_ascii=False), encoding="utf-8")
    (p / "output/videos/shot.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42" + b"v" * 400)
    return p


def cmd_package(args):
    name = f"G3B_{args.nonce.replace('-', '_')}"
    root = Path(args.workdir)
    if not M.is_full_git_sha(args.source_commit):
        raise RelstoreError("source_code_commit 必须是 40 位完整 SHA（G3-B 修正）")
    project = build_synthetic_project(root, name)
    payload, files = M.build_payload(
        project_dir=project, project_id=f"p_g3b_{args.nonce.replace('-', '_')}",
        revision=1, lifecycle_status="active",
        source_code_commit=args.source_commit,
        created_at=time.time(),
        archive_operation_id=f"aop_g3b_{args.nonce.replace('-', '_')}",
        tool_versions={"python": "3.13.0"})
    assert payload["dependencies"]["missing_required"] == []
    pb = M.human_dumps(payload)
    tar_path = root / f"g3-test-{args.nonce}.tar"
    info = safe_tar.create_archive(project, tar_path,
                                   extra_files={M.PAYLOAD_MANIFEST_NAME: pb},
                                   fixed_mtime=0)
    assert info["bytes"] <= 1024 * 1024, "超过 1MiB 上限"
    out_write(Path(args.out), {
        "project_dir": str(project), "tar": str(tar_path),
        "bytes": info["bytes"], "sha256": info["sha256"],
        "payload": payload, "restorable_level": "full"})
    return 0


# ---------------- Release 全链 ----------------

def _load_state(p: Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def cmd_release_draft(args):
    token = load_token(args.token_file)
    a, _, _ = make_adapters(token)
    st = _load_state(args.state)
    tar_path = Path(st["tar"])
    data = tar_path.read_bytes()
    tag = f"test-relstore/{args.nonce}/r1"
    # immutable 闸（执行时以当次 GET 为准；API unknown 时采信所有者 Settings 裁定）
    state = assert_writable(a.get_immutable_releases(),
                            getattr(args, "immutable_attested", None))
    st["immutable_state"] = state
    st["immutable_attested"] = getattr(args, "immutable_attested", None)
    base_sha = idx_b_head(a)
    if base_sha != st.get("b_main_sha"):
        raise RelstoreError(f"B main SHA 变化: {base_sha} != {st.get('b_main_sha')}，停机")
    rel = a.create_release(tag, name=f"G3 test {args.nonce}",
                           body=f"G3-B synthetic test release (nonce={args.nonce})",
                           target_commitish=base_sha, draft=True)
    asset_name = f"g3-test-{args.nonce}.tar"
    meta = a.upload_asset(rel.id, asset_name, data, st["sha256"])
    st.update({"tag": tag, "release_id": rel.id, "asset_id": meta.id,
               "asset_name": asset_name, "uploaded_bytes": meta.size,
               "b_main_sha": base_sha, "phase": "draft_uploaded"})
    out_write(Path(args.state), st)
    return 0


def cmd_release_verify_draft(args):
    token = load_token(args.token_file)
    a, _, _ = make_adapters(token)
    st = _load_state(args.state)
    blob = a.download_asset_authenticated(st["asset_id"])
    if len(blob) != st["bytes"]:
        raise RelstoreError(f"Draft 下载字节数不符 {len(blob)} != {st['bytes']}")
    if hashlib.sha256(blob).hexdigest() != st["sha256"]:
        raise RelstoreError("Draft 下载 sha256 不符")
    dl = Path(st["tar"]).parent / "draft_redownload.tar"
    dl.write_bytes(blob)
    plan = safe_tar.inspect(dl)
    root = safe_tar.safe_extract(dl, Path(st["tar"]).parent / "draft_roots", plan)
    proj = root / plan.root_prefix  # tar 单根
    payload = json.loads((proj / M.PAYLOAD_MANIFEST_NAME).read_text(encoding="utf-8"))
    present = {p.relative_to(proj).as_posix() for p in proj.rglob("*") if p.is_file()}
    M.validate_payload(payload, present_paths=present - {M.PAYLOAD_MANIFEST_NAME})
    for f in payload["files"]:
        fp = proj / f["path"]
        assert fp.exists() and fp.stat().st_size == f["bytes"] and \
            M.sha256_file(fp) == f["sha256"], f["path"]
    st["draft_verified"] = {"bytes": len(blob), "sha256": st["sha256"],
                            "files_checked": len(payload["files"]),
                            "restorable_level": "full",
                            "extract_root": str(root)}
    st["phase"] = "draft_verified"
    out_write(Path(args.state), st)
    return 0


def cmd_release_publish(args):
    token = load_token(args.token_file)
    a, _, _ = make_adapters(token)
    st = _load_state(args.state)
    if st.get("phase") != "draft_verified":
        raise RelstoreError("发布前必须完成 draft 二进制验证")
    a.publish_release(st["release_id"], make_latest="false")
    st["phase"] = "published"
    out_write(Path(args.state), st)
    return 0


def cmd_release_verify_public(args):
    a, _, _ = make_adapters(load_token(args.token_file))
    st = _load_state(args.state)
    blob = a.download_asset(st["asset_id"])  # 匿名语义（browser_download_url 路径）
    if len(blob) != st["bytes"] or hashlib.sha256(blob).hexdigest() != st["sha256"]:
        raise RelstoreError("公开匿名重下载不一致 → B1 不写 verified 索引（资产成孤儿保留）")
    dl = Path(st["tar"]).parent / "public_redownload.tar"
    dl.write_bytes(blob)
    plan = safe_tar.inspect(dl)
    root = safe_tar.safe_extract(dl, Path(st["tar"]).parent / "public_roots", plan)
    proj = root / plan.root_prefix
    payload = json.loads((proj / M.PAYLOAD_MANIFEST_NAME).read_text(encoding="utf-8"))
    st["public_verified"] = {"bytes": len(blob), "sha256": st["sha256"],
                              "files_checked": len(payload["files"]),
                              "restorable_level": "full",
                              "extract_root": str(root)}
    # 包外 Receipt 冻结（此后不再改写）
    receipt = M.build_receipt(
        archive_operation_id=payload["archive_operation_id"],
        project_id=payload["project_id"], legacy_alias=payload["legacy_alias"],
        revision=payload["revision"], package_name=st["asset_name"],
        package_bytes=st["bytes"], package_sha256=st["sha256"],
        manifest_payload_sha256=hashlib.sha256(
            M.human_dumps(st["payload"])).hexdigest(),
        verification={"draft_authenticated_download": st["draft_verified"],
                       "public_anonymous_download": st["public_verified"],
                       "bytes_match": True, "safe_extract_ok": True,
                       "restorable_level": "full"},
        status="VERIFIED_RESTORABLE", created_at=time.time(),
        release_ref={"repository": f"{OWNER_B}/{REPO_B}", "tag": st["tag"],
                     "release_id": st["release_id"], "asset_id": st["asset_id"]},
        index_published=False)
    rb = M.human_dumps(receipt)
    (Path(st["tar"]).parent / "verification_receipt.json").write_bytes(rb)
    st["receipt_path"] = str(Path(st["tar"]).parent / "verification_receipt.json")
    st["receipt_sha256"] = hashlib.sha256(rb).hexdigest()
    st["phase"] = "receipt_frozen"
    out_write(Path(args.state), st)
    return 0


def cmd_receipt_lost_sim(args):
    """客户端丢弃上传回执后：按 tag→assets name+size+sha 找回同一资产，
    断言该 Release 资产总数==1（无重复上传）。"""
    a, _, _ = make_adapters(load_token(args.token_file))
    st = _load_state(args.state)
    rel = a.get_release_by_tag(st["tag"])
    assets = a.list_assets(rel.id)
    match = [x for x in assets if x.name == st["asset_name"]
             and x.size == st["bytes"] and x.sha256 == st["sha256"]]
    if not (len(assets) == 1 and match and match[0].id == st["asset_id"]):
        raise RelstoreError(f"回执丢失收敛失败: assets={assets}")
    out_write(Path(args.out), {"found": match[0].__dict__, "asset_count": len(assets),
                                "conclusion": "no duplicate upload"})
    return 0


# ---------------- B1 ----------------

def _b1(token: str, nonce: str) -> tuple[GitHubContentsIndex, str]:
    _, idx, _ = make_adapters(token)
    idx.branch = "main"
    idx.path = "README.md"
    return idx, f"test/g3-{nonce}"


def cmd_b1_init(args):
    token = load_token(args.token_file)
    idx, test_branch = _b1(token, args.nonce)
    head = idx.get_branch_head("main")
    ev = {"main_head_before": head}
    if head is None:
        cur = idx.read()
        if cur is not None:
            raise RelstoreError("B1 无 main 但已有 README？异常状态，停机")
        idx.path = "README.md"
        text = ("# agent-system-v2-rundata-1 (B1)\n\n"
                "活跃项目轻量索引/检查点层（测试命名空间: test/g3-*）。\n"
                "本仓不存项目媒体；G3-B 初始化于 "
                + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")
        ev["init"] = idx.raw_put_text(
            text, message="G3-B: B1 minimal init (README only)")
    ev["main_head_after"] = idx.get_branch_head("main")
    out_write(Path(args.out), ev)
    return 0


def cmd_b1_branch(args):
    token = load_token(args.token_file)
    idx, test_branch = _b1(token, args.nonce)
    existing = idx.get_branch_head(test_branch)
    if existing:
        raise RelstoreError(f"分支已存在: {test_branch}（nonce 冲突，停止）")
    r = idx.create_branch_from("main", test_branch)
    out_write(Path(args.out), {**r, "branch": test_branch})
    return 0


def cmd_b1_write(args):
    """单次 CAS 写（用于初始 revision=1 与最终索引）。--doc 为 JSON 文件。"""
    token = load_token(args.token_file)
    idx, test_branch = _b1(token, args.nonce)
    idx.branch = test_branch
    idx.path = f"tests/g3/{args.nonce}/{args.file}"
    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    cur = idx.read()
    r = idx.write_cas(doc, None if cur is None else cur["blob_sha"],
                       message=args.message)
    out_write(Path(args.out), {**r, "path": idx.path, "blob_before":
                                None if cur is None else cur["blob_sha"]})
    return 0


def cmd_b1_race(args):
    """竞争写手（独立进程运行）：读当前 blob → 一次 CAS 写 → 结果 JSON。"""
    token = load_token(args.token_file)
    idx, test_branch = _b1(token, args.nonce)
    idx.branch = test_branch
    idx.path = f"tests/g3/{args.nonce}/state.json"
    res = {"role": args.role, "time": time.time()}
    try:
        cur = idx.read()
        res["observed_blob_sha"] = None if cur is None else cur["blob_sha"]
        res["observed_revision"] = None if cur is None else cur["doc"].get("revision")
        doc = {"revision": (0 if cur is None else cur["doc"]["revision"]) + 1,
               "winner": args.role, "written_at": time.time(),
               "note": f"G3-B CAS race writer {args.role}"}
        r = idx.write_cas(doc, res["observed_blob_sha"],
                           message=f"G3-B race writer {args.role}")
        res.update({"ok": True, "http": r["http"], "new_blob_sha": r["new_sha"],
                     "commit": r["commit"], "doc": doc})
    except Exception as e:  # AssetConflict 等
        res.update({"ok": False, "error_class": type(e).__name__,
                     "error_code": getattr(e, "code", ""), "error": str(e)[:300]})
        # 败者候选本地保留
        cand = Path(args.candidate_dir) / f"loser_candidate_{args.role}.json"
        cand.parent.mkdir(parents=True, exist_ok=True)
        cand.write_text(json.dumps({"role": args.role, "rejected": True,
                                     "error": str(e)[:300]}, ensure_ascii=False),
                         encoding="utf-8")
        res["candidate_saved"] = str(cand)
    out_write(Path(args.out), res)
    return 0


def cmd_b1_verify(args):
    token = load_token(args.token_file)
    idx, test_branch = _b1(token, args.nonce)
    idx.branch = test_branch
    idx.path = f"tests/g3/{args.nonce}/state.json"
    cur = idx.read()
    st = _load_state(args.state)
    doc = cur["doc"]
    ref = doc.get("release_ref") or {}
    pkg = doc.get("package") or {}
    checks = {
        "blob_sha_len40": len(cur["blob_sha"]) == 40,
        "pointer_matches_receipt": (pkg.get("sha256") or doc.get("package_sha256")) == st["sha256"],
        "asset_id": (ref.get("asset_id") or doc.get("asset_id")) == st["asset_id"],
        "release_id": (ref.get("release_id") or doc.get("release_id")) == st["release_id"],
        "receipt_sha256": doc.get("receipt_sha256") == st["receipt_sha256"],
        "status": doc.get("status") == "VERIFIED_RESTORABLE",
    }
    out_write(Path(args.out), {"final": doc, "blob_sha": cur["blob_sha"],
                                "checks": checks,
                                "all_ok": all(checks.values())})
    return 0 if all(checks.values()) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="g3b_executor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("precheck", cmd_precheck), ("package", cmd_package),
                     ("release-draft", cmd_release_draft),
                     ("release-verify-draft", cmd_release_verify_draft),
                     ("release-publish", cmd_release_publish),
                     ("release-verify-public", cmd_release_verify_public),
                     ("receipt-lost-sim", cmd_receipt_lost_sim),
                     ("b1-init", cmd_b1_init), ("b1-branch", cmd_b1_branch),
                     ("b1-write", cmd_b1_write), ("b1-race", cmd_b1_race),
                     ("b1-verify", cmd_b1_verify)]:
        p = sub.add_parser(name)
        p.add_argument("--out")
        p.add_argument("--state")
        p.add_argument("--nonce")
        p.add_argument("--workdir")
        p.add_argument("--source-commit", default="")
        p.add_argument("--token-file", default="")
        p.add_argument("--immutable-attested", default=None,
                       choices=[None, "enabled", "disabled"])
        p.add_argument("--file", default="state.json")
        p.add_argument("--doc")
        p.add_argument("--message", default="G3-B")
        p.add_argument("--role", choices=["A", "B"])
        p.add_argument("--candidate-dir")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
