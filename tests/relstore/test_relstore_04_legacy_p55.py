# tests/relstore/test_relstore_04_legacy_p55.py
# 矩阵 4：真实 P55_SMOKE2B 旧包（无包内 manifest）legacy 同名恢复 +
# 真实仓代码 ProjectStore/OCC/master_lease/JobQueue 的零外呼 mock 续做。
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from tools.relstore import restore_campaign

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

P55_SHA = "f18a80b3a2b60b4ed762ef9aec70edb85f9124147dd8cdc1f7f38779dd2b75ff"
G1_TAR = Path(r"C:\Users\Public\Documents\AgentV2_Release_POC_20261021\P55_SMOKE2B.download.tar")
B_TAR = Path(r"C:\Users\曹文淇\Desktop\ai工作流\agent-system-v2-rundata\films\P55_SMOKE2B.tar")
# 注：用户名路径含中文，下面用显式拼接避免编码猜测
B_TAR = Path("C:/Users/曹文淇/Desktop/ai工作流/agent-system-v2-rundata/films/P55_SMOKE2B.tar")
# CI runner 上没有本机路径，用 P55_TAR_PATH 传入样本位置（跨平台）；
# 未设置时回落到上面两个本机路径，本地开发行为完全不变。
P55_TAR_ENV = "P55_TAR_PATH"


def _p55_candidates():
    cands = []
    env = (os.environ.get(P55_TAR_ENV) or "").strip()
    if env:
        cands.append(Path(env))
    cands.extend((G1_TAR, B_TAR))
    return cands


@pytest.fixture(scope="module")
def p55_tar():
    chosen = None
    for cand in _p55_candidates():
        if cand.exists():
            h = hashlib.sha256()
            with open(cand, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() == P55_SHA:
                chosen = cand
                break
    if chosen is None:
        pytest.skip("P55_SMOKE2B.tar（G1 下载包与 B 原包）不可用或 sha 不符")
    return chosen


def test_legacy_restore_same_name(p55_tar, dest_parent):
    rep = restore_campaign.restore_package(
        p55_tar, dest_parent, expected_sha256=P55_SHA,
        source_note="D Release archive-projects-20261019 asset 656674589")
    assert rep["mode"] == "legacy"
    assert rep["alias"] == "P55_SMOKE2B"
    assert rep["files_checked"] == 90
    assert rep["missing"] == []  # 同名恢复：业务引用零缺失
    assert rep["embedded_refs"] >= 20
    # 旧包缺 input 与 skill 实体 → 只能 partial，绝不标 full
    assert rep["restorable_level"] == "partial"
    miss = rep["missing_required"]
    assert any(m.startswith("skill:full-story-relay-calibration@1.6.0") for m in miss)
    assert any("project_input" in m for m in miss)
    assert rep["lease"]["present"] and rep["lease"]["expired"] is True
    # sidecar 写在解包项目树【之外】（restore_xxx/ 的同级）
    sc = Path(rep["sidecar_path"])
    assert sc.exists()
    project_root = Path(rep["project_root"])
    assert not str(sc).startswith(str(project_root))
    sidecar = json.loads(sc.read_text(encoding="utf-8"))
    assert sidecar["manifest_kind"] == "legacy_archive_sidecar"
    assert sidecar["per_file_evidence"].startswith("sidecar_computed")
    assert len(sidecar["files"]) == 90


def test_p55_video_fingerprints_2_of_2(p55_tar, dest_parent):
    rep = restore_campaign.restore_package(p55_tar, dest_parent / "b",
                                           expected_sha256=P55_SHA)
    root = Path(rep["project_root"])
    seqs = json.loads((root / "output/sequences.json").read_text(encoding="utf-8"))
    pj = json.loads((root / "output/project.json").read_text(encoding="utf-8"))
    vv_local = {}
    for sc in pj["scenes"]:
        for sh in sc["shots"]:
            for vv in sh.get("video_versions", []):
                vv_local[vv["version_id"]] = vv["local"]
    n = 0
    for sv in seqs["sequences"].values():
        for rv in sv["render_versions"]:
            for clip in rv["generation_basis"]["clips"]:
                rel = vv_local[clip["resolved_video_version_id"]].replace("\\", "/")
                rel = rel.split("projects/P55_SMOKE2B/", 1)[1]
                got = hashlib.sha256((root / rel).read_bytes()).hexdigest()
                assert got == clip["source_video_fingerprint"]
                n += 1
    assert n == 2


def test_p55_projectstore_occ_lease_queue_mock(p55_tar, dest_parent, tmp_path):
    """对隔离【可写副本】跑真实仓代码（零外呼：conftest socket 封锁）。"""
    rep = restore_campaign.restore_package(p55_tar, dest_parent / "c",
                                           expected_sha256=P55_SHA)
    import shutil
    work = tmp_path / "work"
    shutil.copytree(rep["project_root"], work / "P55_SMOKE2B")
    proj_dir = work / "P55_SMOKE2B"

    from src.core.store import ProjectStore, ProjectConflictError
    from src.agents import master_lease
    from src.core.job_queue import JobQueue

    store = ProjectStore(proj_dir)
    proj = store.load()
    shots = [sh.shot_id for sc in proj.scenes for sh in sc.shots]
    assert shots == ["s01_c01", "s01_c02", "s02_c01", "s02_c02"]
    sig = proj._store_signature

    # OCC happy path + 陈旧拒绝
    store.save(proj, expected_signature=sig)
    with pytest.raises(ProjectConflictError):
        store.save(proj, expected_signature="0" * 64)

    # 过期租约接管 + 旧 holder fencing
    acq = master_lease.acquire(proj_dir, "sess_g2_new")
    assert acq["ok"] and acq["generation"] == 3
    assert master_lease.verify(proj_dir, "sess_g2_new")["verified"] is True
    assert master_lease.verify(proj_dir, "sess_0ac3611893c2")["verified"] is False

    # JobQueue 隔离假任务（续做未完工镜头 s01_c01）
    async def drain():
        async def handler(job, emit):
            await emit("submit", "mock resume", 50)
        q = JobQueue(work / "jobs.json", {"video": handler})
        await q.start()
        q.submit(project="P55_SMOKE2B", kind="video", shot_id="s01_c01",
                 provider="mock-local", video_model="mock-none")
        for _ in range(200):
            jobs = list(q.jobs.values())
            if jobs and all(j.state in ("done", "failed", "cancelled") for j in jobs):
                break
            await asyncio.sleep(0.02)
        j = next(iter(q.jobs.values()))
        for t in q._tasks:
            t.cancel()
        await asyncio.gather(*q._tasks, return_exceptions=True)
        return j.state
    assert asyncio.run(drain()) == "done"
