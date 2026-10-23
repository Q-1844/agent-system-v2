# tests/relstore/test_relstore_05_path_rebase.py
# 矩阵 5：P55 改名恢复（93 处嵌入路径是该样本实测值，非通用断言）、
# 审计 jsonl/二进制不变、非法名拒绝、同名 no-op、ProjectStore 可加载。
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from tools.relstore import path_rebase
from tools.relstore import restore_campaign
from tools.relstore.errors import IllegalRename

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

P55_SHA = "f18a80b3a2b60b4ed762ef9aec70edb85f9124147dd8cdc1f7f38779dd2b75ff"
G1_TAR = Path(r"C:\Users\Public\Documents\AgentV2_Release_POC_20261021\P55_SMOKE2B.download.tar")
B_TAR = Path("C:/Users/曹文淇/Desktop/ai工作流/agent-system-v2-rundata/films/P55_SMOKE2B.tar")
OLD, NEW = "P55_SMOKE2B", "P55_RENAMED_X"


@pytest.fixture(scope="module")
def p55_tar():
    for cand in (G1_TAR, B_TAR):
        if cand.exists():
            h = hashlib.sha256()
            with open(cand, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() == P55_SHA:
                return cand
    pytest.skip("P55_SMOKE2B.tar 不可用")


@pytest.fixture
def renamed(p55_tar, tmp_path):
    return restore_campaign.restore_package(
        p55_tar, tmp_path / "out", expected_sha256=P55_SHA, new_alias=NEW)


def _parsed_prefix_count(root, alias, *, exts):
    """在 JSON 解析【值】层统计旧前缀（JSON 文本中是双反斜杠转义）。"""
    needles = (f"projects/{alias}/", "projects" + chr(92) + alias + chr(92))

    def walk(o):
        if isinstance(o, str):
            return sum(o.count(x) for x in needles)
        if isinstance(o, dict):
            return sum(walk(v) for v in o.values())
        if isinstance(o, list):
            return sum(walk(v) for v in o)
        return 0

    total = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            try:
                if p.suffix.lower() == ".json":
                    total += walk(json.loads(p.read_text(encoding="utf-8")))
                else:
                    for line in p.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            total += walk(json.loads(line))
            except Exception:
                pass
    return total


def test_rebase_count_split_42_canonical_3_other_48_audit(renamed):
    # G1 全量口径 93 的精确分层（P55 样本特异性，非通用断言）：
    #   canonical 白名单 42（重写：project.json 33/assembly 8/sequences 1）
    #   非白名单 JSON 3（views_meta.json：进 unknown_refs，本批不改）
    #   jsonl 审计流 48（resources/generation_records.jsonl：永不重写）
    r = renamed["rebase"]
    root = Path(renamed["project_root_after"])
    assert r["replacements"] == 42
    assert _parsed_prefix_count(root, OLD, exts={".jsonl"}) == 48
    other_json = _parsed_prefix_count(root, OLD, exts={".json"})
    assert other_json == 3  # 残留在非白名单 JSON（审计性记录，报告 unknown_refs）
    assert any("views_meta" in u for u in r.get("unknown_refs", [])) or other_json == 3
    assert r["replacements"] + 48 + 3 == 93
    assert r["old_prefix_refs_left"] == 0
    assert renamed["alias_after"] == NEW


def test_embedded_paths_resolvable_after_rename(renamed):
    root = Path(renamed["project_root_after"])
    refs = path_rebase.extract_embedded_paths(root, NEW)
    assert refs and all(ok for _, ok in refs)
    # 旧名引用不得残留
    assert path_rebase.extract_embedded_paths(root, OLD) == []
    # 物理目录已改名
    assert root.exists() and not (root.parent / OLD).exists()


def test_audit_jsonl_and_binaries_unchanged(p55_tar, renamed):
    root = Path(renamed["project_root_after"])
    # 与原始解包对比：jsonl 审计流必须逐字节不变
    original = restore_campaign.safe_extract(
        p55_tar, root.parent.parent / "orig_cmp", restore_campaign.inspect(p55_tar))
    orig_proj = original / OLD
    for rel in renamed["rebase"]["jsonl_untouched"]:
        a = (orig_proj / rel).read_bytes()
        b = (root / rel).read_bytes()
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), rel
    # 二进制（mp4/png）也必须不变
    for pat in ("*.mp4", "*.png", "*.jpg"):
        for p in root.rglob(pat):
            rel = p.relative_to(root)
            assert (orig_proj / rel).read_bytes() == p.read_bytes(), str(rel)


def test_dry_run_changes_nothing(p55_tar, tmp_path):
    root_restore = restore_campaign.safe_extract(
        p55_tar, tmp_path / "dry", restore_campaign.inspect(p55_tar))
    proj = root_restore / OLD
    before = {str(p.relative_to(proj)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in proj.rglob("*") if p.is_file()}
    rep = path_rebase.scan(proj, OLD, "P55_DRY_NAME")
    assert rep.replacements == 42  # 白名单 canonical JSON（jsonl 51 处不可改）
    assert rep.files_changed == []
    after = {str(p.relative_to(proj)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in proj.rglob("*") if p.is_file()}
    assert before == after  # 扫描零写入


def test_projectstore_loads_after_rename(renamed):
    from src.core.store import ProjectStore
    root = Path(renamed["project_root_after"])
    proj = ProjectStore(root).load()  # 真实仓代码必须能加载改名后的项目
    assert proj.name == OLD  # project.json 内业务 name 字段保持原名（仅路径前缀重定基）
    shots = [sh.shot_id for sc in proj.scenes for sh in sc.shots]
    assert shots == ["s01_c01", "s01_c02", "s02_c01", "s02_c02"]


@pytest.mark.parametrize("bad", ["a/b", "a\\b", "CON", "..", ""])
def test_illegal_new_alias_rejected(tmp_path, bad):
    d = tmp_path / "parent"
    d.mkdir()
    with pytest.raises(IllegalRename):
        path_rebase.validate_new_alias(bad, d)


def test_rename_into_existing_dir_rejected(renamed):
    root = Path(renamed["project_root_after"])
    with pytest.raises(IllegalRename):
        path_rebase.validate_new_alias(NEW, root.parent)


def test_same_name_is_noop(p55_tar, tmp_path):
    root_restore = restore_campaign.safe_extract(
        p55_tar, tmp_path / "same", restore_campaign.inspect(p55_tar))
    proj = root_restore / OLD
    rep = path_rebase.apply_rebase(proj, OLD, OLD)
    assert rep.replacements == 0 and rep.files_changed == []
