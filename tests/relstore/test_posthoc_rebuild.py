# tests/relstore/test_posthoc_rebuild.py
# 🔴 ink-diffusion-20261107 防回退结构锁：posthoc 的 starter 僵尸重建路径。
# 根因：2GB project.tar POST 201 假回执 → GitHub finalize 永久卡 starter →
# 资产 404。教训：posthoc 必须识别僵尸并从 e2e-evidence artifact 重打包
# sha 命名替代资产，绝不把 starter 当完好资产验证/复用。
# 红了先怀疑自己改了不该改的（详见 docs/HANDOFF_ink_endframe_retry_20261106.md 同日补章）。
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_posthoc_rejects_zombie_without_rebuild_from():
    """僵尸场景必须强制要求 --rebuild-from，不得静默验证 404 资产。"""
    src = (REPO / "tools/posthoc_complete.py").read_text(encoding="utf-8")
    assert "starter 僵尸" in src
    assert "state != \"uploaded\"" in src
    assert "--rebuild-from" in src
    # 僵尸作为故障证据保留，绝不删除/覆盖
    assert "kept as evidence" in src
    # 替代资产以 sha 命名，避开僵尸同名（422 → starter 拒用）
    assert "project-rebuilt-" in src


def test_posthoc_rebuild_uploads_with_self_verification():
    """重建资产必须回读自证（redownload + sha），不得只信上传回执。"""
    src = (REPO / "tools/posthoc_complete.py").read_text(encoding="utf-8")
    assert "rebuilt package redownload sha mismatch" in src
    assert "not_found_grace_seconds=240.0" in src


def test_posthoc_workflow_wires_artifact_download():
    """workflow 必须有 artifact 下载 + rebuild 传参接线。
    （2026-11-07 实测：download-artifact@v4 的 GITHUB_TOKEN 跨 run 下载拒之，
    42384281634 实锤；改走 B1_ARCHIVE_TOKEN + REST API，勿改回。）"""
    yml = (REPO / ".github/workflows/posthoc-complete.yml").read_text(
        encoding="utf-8")
    assert "evidence_artifact_run_id" in yml
    assert "actions/artifacts" in yml  # REST API 直下，非 download-artifact
    assert "unzip -q evidence-artifact.zip" in yml
    assert "actions: read" in yml
    assert "--rebuild-from" in yml


def test_adapter_finalize_confirmation_structure_locked():
    """适配器防回退锁（与 test_relstore_07 呼应，双保险）：finalize 复核
    与 starter 拒用是同一根因的两面，缺一不可。"""
    src = (REPO / "tools/relstore/github_release_adapter.py").read_text(
        encoding="utf-8")
    assert "_require_asset_finalized" in src
    assert 'existing.state == "starter"' in src
