# tests/relstore/test_relstore_03_archive_state.py
# 矩阵 6/7/8：状态机全链、Fake 故障（429/5xx/401/403/中断/回执丢失/损坏下载/索引失败）、
# 幂等重入、同名异 sha、revision 守卫、依赖缺失降级、磁盘不足、终态粘性。
import json

import pytest

from tools.relstore import archive_campaign as A
from tools.relstore import manifest as M
from tools.relstore.errors import (AssetConflict, AuthError, HashMismatch,
                                  IndexPublishFailed, RateLimited,
                                  SourceRevisionChanged, UnsafeArchive)
from tools.relstore.fake_release_adapter import FakeReleaseAdapter

TAG = "proj-archive/p_test/r1"
ASSET = "p_test_r1.tar"


def _run_plan(sample_project, opdir, *, project_id="p_test", revision=1):
    return A.plan(project_dir=sample_project, opdir=opdir,
                  operation_id="aop_t", project_id=project_id, revision=revision,
                  lifecycle_status="active", source_code_commit="fc8da19",
                  created_at=1792242000.0,
                  tool_versions={"python": "3.13.0", "ci_lock": "requirements-ci.txt@x"})


def _full_project(sample_project):
    """依赖齐全变体：移除 skill trace（无外部 skill 引用），input 已存在。"""
    (sample_project / "output/agents/skills/trace.jsonl").unlink()
    return sample_project


def test_happy_path_full_restorable(sample_project, opdir):
    _full_project(sample_project)
    st = _run_plan(sample_project, opdir)
    st = A.package(opdir)
    assert st["state"] == A.PACKAGED
    adapter = FakeReleaseAdapter()
    st = A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_RESTORABLE
    # 资产与 release 各 1
    assets = adapter.call_log()
    assert [x["method"] for x in assets].count("upload_asset") == 1
    # receipt 在包外，含 tar 关闭后哈希，无自哈希
    rcpt = json.loads((opdir / M.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert rcpt["package"]["sha256"] == st["package"]["sha256"]
    assert "receipt_sha256" not in rcpt
    assert rcpt["verification"]["bytes_match"] is True
    assert rcpt["verification"]["restorable_level"] == "full"
    assert rcpt["index_published"] is True
    # B1 模拟索引最后发布
    assert adapter.published_indexes["p_test"]["status"] == A.VERIFIED_RESTORABLE


def test_missing_skill_stays_partial(sample_project, opdir):
    # sample_project 自带 trace（3 个 skill load，无实体）→ 缺必需依赖
    st = _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter()
    st = A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_BYTES  # 不冒充完全可恢复
    rcpt = json.loads((opdir / M.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert rcpt["verification"]["restorable_level"] == "partial"
    miss = rcpt["verification"]["missing_required"]
    assert any(x.startswith("skill:full-story-relay-calibration@1.6.0") for x in miss)
    assert adapter.published_indexes["p_test"]["status"] == A.VERIFIED_BYTES


def test_corrupt_redownload_fails_bytes_state(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(
        failures={"download_asset": ["download_corrupt_once"]})
    with pytest.raises(HashMismatch):
        A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    st = A.load_state(opdir)
    assert st["state"] == A.FAILED and "hash" in st["error"]
    # 源树与包都未被删除
    assert (sample_project / "output/project.json").exists()
    assert (opdir / "aop_t.tar").exists()


def test_index_publish_failure_makes_orphan_then_recover(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(
        failures={"publish_index": ["index_fail_once"]})
    with pytest.raises(IndexPublishFailed):
        A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    st = A.load_state(opdir)
    assert st["state"] == A.INDEX_PENDING
    # 资产已上传可被孤儿扫描发现；源件未动
    orphans = adapter.find_orphan_assets("p_test")
    assert len(orphans) == 1 and orphans[0].name == ASSET
    assert adapter.published_indexes == {}
    # 重入收养
    st = A.recover_index(opdir, adapter, sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_RESTORABLE
    assert adapter.published_indexes["p_test"]["asset_id"] == orphans[0].id


def test_idempotent_rerun_single_asset(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter()
    A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    # 重入（迟到回执/调用方重试）：同名同 sha 复用，不制造第二资产
    A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    rel = adapter._releases[TAG]
    assert len(rel["assets"]) == 1
    uploads = [x for x in adapter.call_log() if x["method"] == "upload_asset"]
    assert len(uploads) == 1, "重入走 get_asset 复用，不得再次上传"


def test_same_name_different_sha_conflicts(sample_project, opdir, tmp_path):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter()
    A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    # 同 release 下同名资产异 sha：必须 CONFLICT，绝不覆盖
    with pytest.raises(AssetConflict):
        rel = adapter._releases[TAG]["handle"]
        import hashlib
        adapter.upload_asset(rel.id, ASSET, b"different-bytes",
                             hashlib.sha256(b"different-bytes").hexdigest())
    # 原资产字节未被动
    ast = adapter.list_assets(rel.id)[0]
    assert ast.sha256 == A.load_state(opdir)["package"]["sha256"]


def test_source_revision_change_supersedes_and_deletes_nothing(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    # 打包后、verify 前源项目被修改
    pj = sample_project / "output/project.json"
    original = pj.read_bytes()
    doc = json.loads(original)
    doc["updated_at"] = 9999999999.0
    pj.write_text(json.dumps(doc), encoding="utf-8")
    adapter = FakeReleaseAdapter()
    with pytest.raises(SourceRevisionChanged):
        A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    st = A.load_state(opdir)
    assert st["state"] == A.SUPERSEDED
    # 旧包不得成为删除任何东西的许可：包、源、op 全部保留
    assert (opdir / "aop_t.tar").exists()
    assert pj.exists()
    assert adapter._releases == {}  # 变化检测在上传之前，未产生远端写入


def test_rate_limited_then_retried(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(failures={"upload_asset": [FakeReleaseAdapter.rate_limited(0.0)]})
    st = A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_RESTORABLE
    calls = [x for x in adapter.call_log() if x["method"] == "upload_asset"]
    assert len(calls) == 2  # 一次 429 + 重试成功


def test_server_error_then_retried(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(failures={"upload_asset": [FakeReleaseAdapter.server_error()]})
    st = A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_RESTORABLE


def test_auth_error_no_retry(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(failures={"upload_asset": [FakeReleaseAdapter.auth_error()]})
    with pytest.raises(AuthError):
        A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    calls = [x for x in adapter.call_log() if x["method"] == "upload_asset"]
    assert len(calls) == 1  # 401/403 禁止盲重试


def test_receipt_lost_reentry(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(failures={"get_asset": ["receipt_lost"]})
    st = A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_RESTORABLE
    rel = adapter._releases[TAG]
    assert len(rel["assets"]) == 1  # 未重复制造


def test_network_interrupt_upload_retried(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(failures={"upload_asset": [FakeReleaseAdapter.network_interrupt()]})
    st = A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    assert st["state"] == A.VERIFIED_RESTORABLE
    assert len(adapter._releases[TAG]["assets"]) == 1


def test_disk_full_blocks_extract(sample_project, opdir, monkeypatch):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter()

    import tools.relstore.safe_tar as st_mod
    real = st_mod.shutil.disk_usage

    def tiny(path):
        class U: free = 1024  # 1KiB
        return U()
    monkeypatch.setattr(st_mod.shutil, "disk_usage", tiny)
    with pytest.raises(UnsafeArchive, match="insufficient_disk_space"):
        A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)


def test_terminal_state_not_rolled_back_by_late_failure(sample_project, opdir):
    _full_project(sample_project)
    _run_plan(sample_project, opdir)
    A.package(opdir)
    adapter = FakeReleaseAdapter(failures={"publish_index": ["index_fail_once"]})
    with pytest.raises(IndexPublishFailed):
        A.verify(opdir, adapter, release_tag=TAG, asset_name=ASSET, sleep=lambda s: None)
    A.recover_index(opdir, adapter, sleep=lambda s: None)
    st0 = A.load_state(opdir)
    assert st0["state"] == A.VERIFIED_RESTORABLE
    # 迟到的旧失败：recover 对非 INDEX_PENDING 状态原样返回，不回退
    st1 = A.recover_index(opdir, adapter, sleep=lambda s: None)
    assert st1["state"] == A.VERIFIED_RESTORABLE
