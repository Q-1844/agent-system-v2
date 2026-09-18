"""Agent System V2 Phase B — Commit B：ProjectVisualProfile 对齐测试（26.md §3）。

覆盖：
- B1 (R5) Resource Agent 能 read profile（specified 返回内容+指纹+revision；
         unspecified 明确状态；损坏 fail closed 不抛穿）；
- B2 (R6) agent 三视图生成（handler）实际消费 profile——prompt 含编译点风格指令
         （与 HTTP _apply_project_style 同一唯一编译点）；unspecified 项目不追加（宽容
         降级与 HTTP 行为一致）；
- B3 (R7) 不再使用冲突 legacy 风格——appearance_sig 纳入 profile 指纹：profile 变更
         ⇒ sig 变（触发重生成）；unspecified 稳定；
- B4 (R8) Agent 无 profile mutation 工具；
- B5 agent executor 提交的 appearance_sig = 三元签名（含 profile 指纹）。

全部 deterministic：Agnes/segmentor/下载在边界 mock，0 网络调用。
"""
import asyncio
import json

import pytest

import deps
from src.core.models import Project, Character, StyleAnchor, Asset
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import character_views_handler as cvh
from src.agents.character_designer import (
    _execute_visual_profile_read, _visual_profile_fingerprint_or_empty)
from src.agents.character_views_handler import (
    character_appearance_sig, handler_character_views)
from src.visual_profile import VisualProfileService
from src.visual_profile.compiler import append_style_instruction
from src.visual_profile import load_profile_or_none
from routers.characters import _build_view_prompt


def _runtime(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _make_project(tmp_path):
    proj = Project(name="demo", style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   characters=[Character(name="Xiaoyu",
                                         description_en="a girl with short hair")])
    ProjectStore(tmp_path).save(proj)
    return tmp_path


def _set_profile(pdir, render_family="photorealistic"):
    VisualProfileService(pdir, pdir.name).update(render_family=render_family,
                                                 expected_revision="")


# ============ B1 (R5)：project.visual_profile.read ============
def test_B1_read_profile_specified(tmp_path):
    _make_project(tmp_path)
    _set_profile(tmp_path)
    rt = _runtime(tmp_path)
    out = _execute_visual_profile_read(rt, "demo", "{}", "读一下视觉定义", None)
    assert out["ok"] is True and out["specified"] is True
    assert out["visual_profile"]["render_family"] == "photorealistic"
    assert out["fingerprint"]
    assert out["revision"]
    # 指纹与独立计算一致（同一唯一真相）
    vp = load_profile_or_none(tmp_path)
    from src.visual_profile.models import visual_profile_fingerprint, profile_revision
    assert out["fingerprint"] == visual_profile_fingerprint(vp)
    assert out["revision"] == profile_revision(vp)


def test_B1b_read_profile_unspecified(tmp_path):
    _make_project(tmp_path)
    rt = _runtime(tmp_path)
    out = _execute_visual_profile_read(rt, "demo", "{}", None, None)
    assert out["ok"] is True and out["specified"] is False
    assert out["visual_profile"] is None
    assert out["fingerprint"] == "" and out["revision"] == ""


def test_B1c_read_profile_corrupt_fail_closed(tmp_path):
    _make_project(tmp_path)
    (tmp_path / "output").mkdir(exist_ok=True)
    (tmp_path / "output" / "project_profile.json").write_text("{broken json", encoding="utf-8")
    rt = _runtime(tmp_path)
    out = _execute_visual_profile_read(rt, "demo", "{}", None, None)
    assert out["ok"] is False
    assert out["error"] == "visual_profile_unreadable"


def test_B1d_no_profile_mutation_tool_R8():
    """R8：Agent 无 profile mutation 工具。"""
    from src.agents import character_designer as cd
    names = {t["function"]["name"] for t in cd.CHARACTER_DESIGNER_TOOLS}
    assert "project.visual_profile.read" in names
    assert not any("visual_profile" in n and "read" not in n for n in names), \
        f"不得出现 profile 写工具: {names}"
    assert "project.visual_profile.read" in cd.CHARACTER_READ_TOOLS
    assert "project.visual_profile.read" not in cd.CHARACTER_WRITE_OPERATIONS


# ============ B2 (R6)：agent 三视图生成实际消费 profile ============
class _FakeGenFull:
    """记录完整 prompt 的假图片客户端。"""

    def __init__(self):
        self.calls = []

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append(prompt)
        return {"url": f"https://fake/{len(self.calls)}.png", "b64": None}

    async def aclose(self):
        pass


class _FakeSeg:
    def remove_background(self, raw):
        return b"\x89PNG-fake-seg", {}


async def _emit(*a, **k):
    pass


def _run_handler(tmp_path, fake):
    proj = ProjectStore(tmp_path).load()
    views = {v: Asset(url=f"https://old/{v}.png", local=None, status="pending")
             for v in ("front", "side", "back")}
    proj.characters[0].views = views
    ProjectStore(tmp_path).save(proj)

    class _M:
        def get_project_dir(self, name):
            return tmp_path

    import routers.characters as rc
    old_pm = getattr(deps, "get_project_manager")
    old_ag = getattr(deps, "agnes_client", None)
    old_get_seg = rc._get_segmentor
    old_dl = cvh._download
    deps.get_project_manager = lambda: _M()
    deps.agnes_client = fake
    rc._get_segmentor = lambda: _FakeSeg()

    async def _dl(url, proxy=None):
        return b"rawbytes"

    cvh._download = _dl
    from src.core.job_queue import Job
    try:
        job = Job(job_id="j_b2", project=tmp_path.name, kind="character_views",
                  params={"character_name": "Xiaoyu"})
        asyncio.run(handler_character_views(job, _emit))
    finally:
        deps.get_project_manager, deps.agnes_client = old_pm, old_ag
        rc._get_segmentor = old_get_seg
        cvh._download = old_dl
    return fake


def test_B2_handler_prompt_consumes_profile(tmp_path, monkeypatch):
    _make_project(tmp_path)
    _set_profile(tmp_path, "2d_animation")
    fake = _run_handler(tmp_path, _FakeGenFull())
    assert len(fake.calls) == 3
    expected = append_style_instruction(
        _build_view_prompt("a girl with short hair", "anime", "front"),
        load_profile_or_none(tmp_path))
    # front prompt 与 HTTP 同一编译点输出一致（Agent 路径 = 正式系统路径）
    assert fake.calls[0] == expected
    assert "Visual production style: 2D animated image" in fake.calls[0]


def test_B2b_handler_prompt_unspecified_no_suffix(tmp_path):
    _make_project(tmp_path)   # 无 profile
    fake = _run_handler(tmp_path, _FakeGenFull())
    expected = _build_view_prompt("a girl with short hair", "anime", "front")
    assert fake.calls[0] == expected, "unspecified 项目不追加风格后缀（与 HTTP 宽容降级一致）"


# ============ B3 (R7)：sig 纳入 profile 指纹 ============
def test_B3_sig_includes_profile_fingerprint():
    base = character_appearance_sig("a girl", "anime", "")
    fp1 = character_appearance_sig("a girl", "anime", "fp-aaa")
    fp2 = character_appearance_sig("a girl", "anime", "fp-bbb")
    assert base != fp1 != fp2, "profile 指纹变化 ⇒ sig 变化（触发重生成）"
    assert character_appearance_sig("a girl", "anime", "fp-aaa") == fp1, "确定性"
    # 兼容：无 profile（""）时同内容同 sig
    assert character_appearance_sig("a girl", "anime") == \
        character_appearance_sig("a girl", "anime", "")


def test_B3b_fp_helper_unspecified_empty():
    # helper：无 profile 项目 → ""
    import tempfile
    from pathlib import Path
    pdir = Path(tempfile.mkdtemp())
    ProjectStore(pdir).save(Project(name="t"))
    assert _visual_profile_fingerprint_or_empty(pdir) == ""
    _set_profile(pdir)
    assert _visual_profile_fingerprint_or_empty(pdir) != ""
    # 损坏文件宽容降级 ""
    (pdir / "output").mkdir(exist_ok=True)
    (pdir / "output" / "project_profile.json").write_text("{bad", encoding="utf-8")
    assert _visual_profile_fingerprint_or_empty(pdir) == ""


# ============ B5：agent executor 的 sig = 三元签名（含 profile 指纹）============
def test_B5_agent_executor_passes_profile_aware_sig(tmp_path, monkeypatch):
    _make_project(tmp_path)
    _set_profile(tmp_path)
    rt = _runtime(tmp_path)
    captured = {}

    async def _fake_handler(job, emit):
        captured["sig"] = job.params.get("appearance_sig")
        return None

    from src.agents import character_designer as cd_module
    monkeypatch.setattr(cd_module, "handler_character_views", _fake_handler)
    from src.agents.character_designer import _generate_one_char_views
    out = asyncio.run(_generate_one_char_views(rt, "demo", "Xiaoyu"))
    # fake handler 没写 canonical → ok=False（验证失败路径如实返回），但 sig 已捕获
    fp = _visual_profile_fingerprint_or_empty(tmp_path)
    assert captured["sig"] == character_appearance_sig("a girl with short hair",
                                                       "anime", fp)
    assert out["ok"] is False, "未真实落盘时 executor 必须诚实返回失败"
