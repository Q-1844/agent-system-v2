# tests/relstore/test_relstore_01_manifest_packaging.py
# 矩阵 1/2：严格 payload、无自哈希、确定性打包、整包 sha 在包外、字节篡改失败。
import json

import pytest

from tools.relstore import manifest as M
from tools.relstore import safe_tar
from tools.relstore.errors import ForbiddenManifestField, ManifestError


def test_payload_is_strict_json_without_comments(sample_project, opdir):
    payload, files = M.build_payload(
        project_dir=sample_project, project_id="p_test", revision=1,
        lifecycle_status="active", source_code_commit="fc8da19",
        created_at=1792236311.0, archive_operation_id="aop_t1",
        tool_versions={"python": "3.13.0"})
    raw = M.human_dumps(payload)
    again = json.loads(raw.decode("utf-8"))  # 严格解析，无 // 注释语法
    assert again["manifest_kind"] == M.PAYLOAD_KIND
    assert again["schema_version"] == 2


def test_files_exclude_manifest_itself(sample_project):
    payload, _ = M.build_payload(
        project_dir=sample_project, project_id="p_test", revision=1,
        lifecycle_status="active", source_code_commit="c", created_at=1.0,
        archive_operation_id="aop_t2", tool_versions={})
    names = [f["path"] for f in payload["files"]]
    assert M.PAYLOAD_MANIFEST_NAME not in names
    M.validate_payload(payload, present_paths=set(names))


def test_forbidden_future_fields_rejected():
    doc = {"schema_version": 2, "manifest_kind": M.PAYLOAD_KIND,
           "archive_operation_id": "a", "project_id": "p", "legacy_alias": "x",
           "revision": 1, "lifecycle_status": "active", "source_code_commit": "c",
           "created_at": 1.0, "project": {}, "files": [
               {"path": "a", "bytes": 1, "sha256": "0" * 64, "role": "other"}],
           "skill_refs": [], "tool_versions": {}, "dependencies": {},
           "external_refs": {}, "security_scan": {}, "empty_dirs": [],
           "packaging": {}}
    for bad in ("archive_sha256", "asset_id", "release_id", "verified_at"):
        d = dict(doc); d[bad] = "future"
        with pytest.raises(ForbiddenManifestField):
            M.validate_payload(d)


def test_manifest_listing_itself_rejected(sample_project):
    payload, _ = M.build_payload(
        project_dir=sample_project, project_id="p", revision=1,
        lifecycle_status="active", source_code_commit="c", created_at=1.0,
        archive_operation_id="a", tool_versions={})
    payload["files"].append(
        {"path": M.PAYLOAD_MANIFEST_NAME, "bytes": 1,
         "sha256": "0" * 64, "role": "other"})
    with pytest.raises(ForbiddenManifestField):
        M.validate_payload(payload)


def test_deterministic_packaging_byte_identical(sample_project, opdir):
    payload, _ = M.build_payload(
        project_dir=sample_project, project_id="p", revision=1,
        lifecycle_status="active", source_code_commit="c", created_at=1.0,
        archive_operation_id="aop_det", tool_versions={})
    pb = M.human_dumps(payload)
    out1 = opdir / "a.tar"
    out2 = opdir / "b.tar"
    i1 = safe_tar.create_archive(sample_project, out1,
                                 extra_files={M.PAYLOAD_MANIFEST_NAME: pb},
                                 fixed_mtime=0)
    i2 = safe_tar.create_archive(sample_project, out2,
                                 extra_files={M.PAYLOAD_MANIFEST_NAME: pb},
                                 fixed_mtime=0)
    assert i1["sha256"] == i2["sha256"]
    assert i1["bytes"] == i2["bytes"]
    assert out1.read_bytes() == out2.read_bytes()


def test_package_sha_computed_after_close_and_receipt_outside(sample_project, opdir):
    payload, _ = M.build_payload(
        project_dir=sample_project, project_id="p", revision=1,
        lifecycle_status="active", source_code_commit="c", created_at=1.0,
        archive_operation_id="aop_rc", tool_versions={})
    pb = M.human_dumps(payload)
    out = opdir / "a.tar"
    info = safe_tar.create_archive(sample_project, out,
                                   extra_files={M.PAYLOAD_MANIFEST_NAME: pb})
    # 整包 sha 只允许出现在包外 receipt
    rcpt = M.build_receipt(
        archive_operation_id="aop_rc", project_id="p",
        legacy_alias=sample_project.name, revision=1,
        package_name="a.tar", package_bytes=info["bytes"],
        package_sha256=info["sha256"],
        manifest_payload_sha256=M.sha256_bytes(pb) if hasattr(M, "sha256_bytes")
        else __import__("hashlib").sha256(pb).hexdigest(),
        verification={"downloaded_bytes": info["bytes"], "bytes_match": True},
        status="VERIFIED_RESTORABLE", created_at=2.0)
    M.validate_receipt(rcpt)
    assert rcpt["package"]["sha256"] == info["sha256"]
    # 包内 payload 不含整包 sha
    inside = json.loads(pb)
    assert "archive_sha256" not in inside


def test_receipt_self_hash_rejected():
    good = dict(schema_version=2, manifest_kind=M.RECEIPT_KIND,
                archive_operation_id="a", project_id="p", legacy_alias="x",
                revision=1, package={"name": "a", "bytes": 1,
                                     "sha256": "0" * 64},
                manifest_payload_sha256="1" * 64,
                verification={}, status="VERIFIED_BYTES", created_at=1.0)
    M.validate_receipt(good)
    bad = dict(good); bad["receipt_sha256"] = "2" * 64
    with pytest.raises(ForbiddenManifestField):
        M.validate_receipt(bad)


def test_single_byte_tamper_fails_restore_check(sample_project, opdir, dest_parent):
    from tools.relstore import restore_campaign
    payload, _ = M.build_payload(
        project_dir=sample_project, project_id="p", revision=1,
        lifecycle_status="active", source_code_commit="c", created_at=1.0,
        archive_operation_id="aop_tamper", tool_versions={})
    # 让依赖齐全（input 已有；消除 skill 缺口：手工置空 skill_refs 与 missing）
    payload["skill_refs"] = []
    payload["dependencies"]["missing_required"] = []
    pb = M.human_dumps(payload)
    out = opdir / "good.tar"
    safe_tar.create_archive(sample_project, out,
                            extra_files={M.PAYLOAD_MANIFEST_NAME: pb})
    good_report = restore_campaign.restore_package(out, dest_parent)
    assert good_report["restorable_level"] in ("full", "partial")
    # 篡改业务文件一个字节后重打包：payload 声明 sha 与实际不符 → blocked/抛错
    (sample_project / "output/resources.json").write_text(
        '{"schema_version": 1, "items": [999]}', encoding="utf-8")
    out2 = opdir / "bad.tar"
    safe_tar.create_archive(sample_project, out2,
                            extra_files={M.PAYLOAD_MANIFEST_NAME: pb})
    with pytest.raises((ManifestError, Exception)):
        restore_campaign.restore_package(out2, dest_parent)
