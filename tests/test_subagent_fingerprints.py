"""fingerprints 单元测试（mock，不联网）。

覆盖：
- scene_slice_fingerprint 确定性 / 内容敏感 / 接力敏感 / shots 域与内容域分离；
- 与 SW 侧 _scene_token、Director 侧 _shots_token 的一致性（防两套口径漂移）；
- compute_scene_basis 三元组。
"""
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.subagent import fingerprints as fp


def _proj():
    s01 = [Shot(shot_id="s01_c01", scene_id="s01", index=1,
                description="雨夜灯塔外景", characters=["林晚星"]),
           Shot(shot_id="s01_c02", scene_id="s01", index=2,
                description="她冲进走廊", characters=["林晚星"],
                source_shot_id="s01_c01", opening_source="inherit_previous")]
    return Project(
        name="demo", style=StyleAnchor(), story_text="正文。",
        characters=[Character(name="林晚星", description_en="a keeper")],
        scenes=[Scene(scene_id="s01", summary="灯塔段", characters=["林晚星"], shots=s01),
                Scene(scene_id="s02", summary="空段", characters=[], shots=[])],
        next_scene_seq=3)


def test_deterministic(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    proj = ProjectStore(tmp_path).load()
    a = fp.scene_slice_fingerprint(proj.scenes[0])
    b = fp.scene_slice_fingerprint(proj.scenes[0])
    assert a == b and len(a) == 8


def test_content_change_changes_fingerprint(tmp_path):
    """段内容（summary）变化 → scene_slice / scene_content 都变。"""
    ProjectStore(tmp_path).save(_proj())
    proj = ProjectStore(tmp_path).load()
    before = fp.scene_slice_fingerprint(proj.scenes[0])
    proj.scenes[0].summary = "改写后的段落"
    after = fp.scene_slice_fingerprint(proj.scenes[0])
    assert before != after


def test_shot_content_change_changes_fingerprint(tmp_path):
    """镜头内容变化 → scene_slice 变（shots 域纳入）；scene_content 不变（域分离）。"""
    ProjectStore(tmp_path).save(_proj())
    proj = ProjectStore(tmp_path).load()
    slice_before = fp.scene_slice_fingerprint(proj.scenes[0])
    content_before = fp.scene_content_token(proj.scenes[0])
    proj.get_shot("s01_c01").description = "雨夜灯塔外景（改）"
    assert fp.scene_slice_fingerprint(proj.scenes[0]) != slice_before
    assert fp.scene_content_token(proj.scenes[0]) == content_before


def test_relay_change_changes_fingerprint(tmp_path):
    """接力变更（source_shot_id）→ scene_slice 变（_shot_token 覆盖接力，0f106c6）。"""
    ProjectStore(tmp_path).save(_proj())
    proj = ProjectStore(tmp_path).load()
    before = fp.scene_slice_fingerprint(proj.scenes[0])
    proj.get_shot("s01_c02").source_shot_id = None   # detach
    assert fp.scene_slice_fingerprint(proj.scenes[0]) != before


def test_other_scene_change_does_not_affect(tmp_path):
    """其它段的改动不影响本段指纹（段级作用域）。"""
    ProjectStore(tmp_path).save(_proj())
    proj = ProjectStore(tmp_path).load()
    before = fp.scene_slice_fingerprint(proj.scenes[0])
    proj.scenes[1].summary = "别的段被改了"
    assert fp.scene_slice_fingerprint(proj.scenes[0]) == before


def test_token_parity_with_existing_impls(tmp_path):
    """与 SW 侧 _scene_token / Director 侧 _shots_token 同源一致（防口径漂移）。"""
    from src.agents.script_writer import _scene_token
    from src.agents.director import _shots_token
    ProjectStore(tmp_path).save(_proj())
    proj = ProjectStore(tmp_path).load()
    assert fp.scene_content_token(proj.scenes[0]) == _scene_token(proj.scenes[0])
    assert fp.shots_structure_token(proj.scenes) == _shots_token(proj.scenes)


def test_scene_content_fields_match_sw_whitelist():
    """SCENE_CONTENT_FIELDS 与 SW 侧 SCENE_UPDATABLE_FIELDS 一致（漂移即失败）。"""
    from src.agents.script_writer import SCENE_UPDATABLE_FIELDS
    assert tuple(fp.SCENE_CONTENT_FIELDS) == tuple(SCENE_UPDATABLE_FIELDS)


def test_compute_scene_basis(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    proj = ProjectStore(tmp_path).load()
    basis = fp.compute_scene_basis(proj.scenes[0], proj.scenes)
    assert set(basis) == {"scene_slice_fingerprint", "scene_content_token",
                          "shots_structure_token"}
    assert all(len(v) == 8 for v in basis.values())


def test_none_scene_empty(tmp_path):
    from src.agents.script_writer import _scene_token
    assert fp.scene_slice_fingerprint(None) == ""
    assert fp.scene_content_token(None) == _scene_token(None)
