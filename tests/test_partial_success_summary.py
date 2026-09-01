# -*- coding: utf-8 -*-
"""V1 收口 · 项2 Partial Success 结束话术 回归测试。

验证三个 Agent 的 _append_fact_summary 据真实 receipts 确定性生成总结，
覆盖全成功 / 部分成功 / 全失败三态，且已成功资产明确列出。纯函数测试
（给定 receipts + 已存项目 → 追加文本），不联网、不调 LLM。
"""
from types import SimpleNamespace

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore


# ---- 三个 Agent 的 _append_fact_summary + op_human ----
from src.agents.script_writer import _append_fact_summary as _sw_summary
from src.agents.character_designer import _append_fact_summary as _cd_summary
from src.agents.director import _append_fact_summary as _dir_summary


class _RT:
    """最小 rt：只实现 _resolve_project_dir（_append_fact_summary 内 _reload 用）。"""
    def __init__(self, pdir):
        self._pdir = pdir

    def _resolve_project_dir(self, project):
        return self._pdir


def _save_proj(tmp_path, *, scenes=None, characters=None):
    proj = Project(name="demo", style=StyleAnchor(), story_text="剧本。",
                   characters=characters or [],
                   scenes=scenes or [],
                   next_scene_seq=1)
    ProjectStore(tmp_path).save(proj)
    return _RT(tmp_path)


# ============ Script Writer ============
def test_sw_all_success(tmp_path):
    rt = _save_proj(tmp_path, scenes=[Scene(scene_id="s01", summary="一", characters=[], shots=[])])
    receipts = [
        {"operation": "story.create", "ok": True, "verified": True, "unchanged": False,
         "scene_id": "s01", "order": 1},
    ]
    out = _sw_summary(rt, "demo", "全部完成。", receipts)
    assert "已完成 1 项" in out
    assert "✓ 创建故事段 s01" in out


def test_sw_partial_success(tmp_path):
    rt = _save_proj(tmp_path, scenes=[])
    receipts = [
        {"operation": "story.create", "ok": True, "verified": True, "unchanged": False,
         "scene_id": "s01", "order": 1},
        {"operation": "story.create", "ok": False, "stale": True, "scene_id": "s02"},
    ]
    out = _sw_summary(rt, "demo", "改好了。", receipts)
    assert "已完成 1 项，1 项未完成/取消" in out
    assert "✓ 创建故事段 s01" in out          # 已成功资产明确保留
    assert "✗ story.create s02" in out
    assert "版本过期" in out


def test_sw_all_failed_crud_no_summary(tmp_path):
    """Script Writer 无视觉生成：纯 CRUD 失败（如删除不存在的段、stale 被拒）且无
    成功变更时，不追加事实块（旧行为，失败原因由错误信息体现）。"""
    rt = _save_proj(tmp_path, scenes=[])
    receipts = [
        {"operation": "story.delete", "ok": False, "error": "not_found", "scene_id": "s99"},
    ]
    out = _sw_summary(rt, "demo", "删掉了。", receipts)   # 模型谎称
    assert "实际执行记录" not in out                       # 纯失败不追加块


# ============ Character Designer ============
def test_cd_partial_success(tmp_path):
    rt = _save_proj(tmp_path, characters=[Character(name="阿默", description_en="x")])
    receipts = [
        {"operation": "character.create", "ok": True, "verified": True, "unchanged": False,
         "character_name": "阿默"},
        {"operation": "character.create", "ok": False, "stale": True, "character_name": "小铃"},
    ]
    out = _cd_summary(rt, "demo", "建好了。", receipts)
    assert "已完成 1 项，1 项未完成/取消" in out
    assert "✓ 创建角色 阿默" in out
    assert "✗ character.create 小铃" in out


def test_cd_crud_fail_no_summary(tmp_path):
    """纯 character.create 失败且无成功变更 → 不追加事实块。"""
    rt = _save_proj(tmp_path, characters=[])
    receipts = [
        {"operation": "character.create", "ok": False, "stale": True, "character_name": "阿默"},
    ]
    out = _cd_summary(rt, "demo", "完成了。", receipts)
    assert "实际执行记录" not in out


def test_cd_generate_views_all_failed(tmp_path):
    """三视图生成全部失败（生成类操作）→ 追加确定性总结"本次没有完成生成"。"""
    rt = _save_proj(tmp_path, characters=[])
    receipts = [
        {"operation": "character.generate_views", "ok": False,
         "error": "agnes_failed", "character_names": ["阿默"]},
    ]
    out = _cd_summary(rt, "demo", "三视图都好了。", receipts)
    assert "本次没有完成生成" in out
    assert "✓" not in out


# ============ Director ============
def test_dir_all_success(tmp_path):
    rt = _save_proj(tmp_path, scenes=[Scene(scene_id="s01", summary="一", characters=[],
                                            shots=[])])
    receipts = [
        {"operation": "shot.create", "ok": True, "verified": True, "unchanged": False,
         "shot_id": "s01_c01"},
    ]
    out = _dir_summary(rt, "demo", "建好了。", receipts)
    assert "已完成 1 项" in out
    assert "✓ 创建镜头 s01_c01" in out


def test_dir_partial_success_generate(tmp_path):
    """视觉生成部分成功：批次 receipt ok=True（含子项），混一个失败 receipt。"""
    rt = _save_proj(tmp_path, scenes=[Scene(scene_id="s01", summary="一", characters=[],
                                            shots=[])])
    receipts = [
        {"operation": "shot.generate_images", "ok": True, "verified": True, "unchanged": False,
         "count": 4, "results": [{"shot_id": "s01_c01", "ok": True, "frame": "first"}]},
        {"operation": "shot.create", "ok": False, "stale": True, "shot_id": "s01_c05"},
    ]
    out = _dir_summary(rt, "demo", "生成完成。", receipts)
    assert "已完成 1 项，1 项未完成/取消" in out
    assert "✓" in out   # 成功的图片生成列出
    assert "✗ shot.create s01_c05" in out


def test_dir_crud_fail_no_summary(tmp_path):
    """纯 shot.create 失败且无成功变更（stale 被拒）→ 不追加事实块。"""
    rt = _save_proj(tmp_path, scenes=[])
    receipts = [
        {"operation": "shot.create", "ok": False, "stale": True, "shot_id": "s01_c01"},
    ]
    out = _dir_summary(rt, "demo", "全部建好了。", receipts)
    assert "实际执行记录" not in out


def test_dir_generate_all_failed(tmp_path):
    """分镜图生成全部失败（生成类操作）→ 追加确定性总结"本次没有完成生成"。"""
    rt = _save_proj(tmp_path, scenes=[])
    receipts = [
        {"operation": "shot.generate_images", "ok": False,
         "error": "agnes_failed", "count": 4, "results": []},
    ]
    out = _dir_summary(rt, "demo", "图都出了。", receipts)
    assert "本次没有完成生成" in out
    assert "✓" not in out


def test_no_receipts_no_block(tmp_path):
    """无写工具调用时不追加事实块（聊天/只读轮）。"""
    rt = _save_proj(tmp_path, scenes=[])
    out = _dir_summary(rt, "demo", "这是回答。", [])
    assert "实际执行记录" not in out
    assert out == "这是回答。"


def test_dir_generate_cancel_partial_summary(tmp_path):
    """多项生成中一部分成功后取消：已成功项保留并产生正确部分成功总结。

    覆盖「cancel/stop 后部分成功 + 正确总结」确定性路径：单张 generate_images 批次
    receipt ok=True/cancelled=True（1 done + 1 cancelled 子项）→ _append_fact_summary
    确定性列出 ✓ 成功项并以 done/count 比例体现部分完成。纯函数，不联 LLM/Provider。
    （cancel 保留资产本身由 test_director_visual.py::test_generate_images_cancel_preserves_done
    在执行器层覆盖；本测试补「总结文本」层。）"""
    rt = _save_proj(tmp_path, scenes=[Scene(scene_id="s01", summary="一", characters=[],
                                            shots=[])])
    receipts = [
        {"operation": "shot.generate_images", "ok": True, "verified": True,
         "unchanged": False, "cancelled": True, "count": 2,
         "results": [{"shot_id": "s01_c01", "ok": True, "frame": "first",
                      "local": "p/s01_c01.png", "verified": True},
                     {"shot_id": "s01_c02", "ok": False, "status": "cancelled"}]},
    ]
    out = _dir_summary(rt, "demo", "图都出了。", receipts)
    assert "✓" in out                       # 成功项明确列出
    assert "1/2" in out                     # 部分完成以 done/count 体现
    assert "s01_c01" in out                 # 已成功资产明确保留（shot_id 可见）
    assert "已完成" in out                  # 确定性总结行
