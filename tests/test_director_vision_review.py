"""Visual Review Tool V1（2026-09-06）· visual.review_image 测试。

不联网（FakeVisionClient）。覆盖任务书 §二十四 的 mock 项（T1-T4 真实 API probe
见 _vision_probe_evidence/ 与 VISUAL_REVIEW_TOOL_V1_REPORT.md，不进单测）：

  - T5 结构化结果：合法 JSON 直接收下；一次 repair 复原；两次失败诚实报错；
    字段非法（verdict/severity/category 拼错）触发 repair；
  - T6 不可访问资产：无生成记录 / 本地文件缺失且无 URL → 结构化失败，绝不假看；
    仅 URL 可用时退回 URL 模式；
  - T7 Director 调用：director_turn 全链路（ScriptedClient 发 visual.review_image
    tool_call → executor 真执行 → 回执进 receipts/trace）；
  - T8 no fake vision：失败路径全部 ok=False + 明确 code，无伪造审查结论；
  - T9 read-only：审查前后 project.json 逐字节不变，除 output/agents/ 遥测外
    项目文件树零变化；
  - context builder：canonical 要求文本、角色设定、图片标签、focus 裁剪；
  - telemetry：usage(含 image_tokens)/latency/repairs/image_count 进回执与
    visual_reviews.jsonl（非 canonical 观测工件）；
  - provider 错误分类：AgnesError → AgentError("provider_error")（重试已在
    client._request 内完成，此处只诚实转译）。
"""
import asyncio
import json
from pathlib import Path

import pytest

from src.api.agnes_client import AgnesError
from src.core.models import (Project, Scene, Shot, Character, StyleAnchor, Asset,
                             Candidate, ImageBatch, ShotStatus)
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.director import (director_turn, DIRECTOR_TOOLS, TOOL_EXECUTORS,
                                 _tool_label, _op_human, _trace_entry)
from src.agents.director_vision_review import (
    VISION_REVIEW_TOOL, _execute_visual_review, FOCUS_ALL,
)

PROJECT = "_test_dir_vision_review"

GOOD_REVIEW = json.dumps({
    "verdict": "needs_fix", "severity": "major",
    "summary": "目镜已点亮，与「尚未点亮」的起始状态不符",
    "issues": [
        {"category": "shot_adherence", "severity": "major",
         "observation": "机甲目镜呈橙黄色发光", "expected": "头部目镜尚未点亮",
         "suggested_fix": "重新生成时明确目镜熄灭"},
    ],
}, ensure_ascii=False)

BAD_REVIEW = "我觉得这张图不错，整体氛围很好，角色也很符合要求。"


# ============ Fakes ============
class FakeVisionClient:
    """AgnesClient 视觉方法 fake：按序弹出脚本响应；记录全部调用供断言。"""

    def __init__(self, vision_responses=None, vision_error=None):
        self.vision_responses = list(vision_responses or [])
        self.vision_error = vision_error
        self.vision_calls = []
        self.chat_script = []          # director_turn 循环用（(content, tool_calls)）

    async def chat_completion_vision(self, messages, *, model, temperature=0.2,
                                     max_tokens=1600):
        self.vision_calls.append({"messages": messages, "model": model,
                                  "temperature": temperature, "max_tokens": max_tokens})
        if self.vision_error is not None:
            raise self.vision_error
        if len(self.vision_responses) > 1:
            r = self.vision_responses.pop(0)
        else:
            r = self.vision_responses[0]
        if isinstance(r, Exception):
            raise r
        return {"content": r,
                "usage": {"prompt_tokens": 1200, "completion_tokens": 180,
                          "total_tokens": 1380,
                          "prompt_tokens_details": {"image_tokens": 943}},
                "model": model}

    async def chat_completion(self, messages, **kw):
        if self.chat_script:
            content, tool_calls = self.chat_script.pop(0)
        else:
            content, tool_calls = "（脚本已耗尽）", None
        return {"content": content, "tool_calls": tool_calls}


# ============ 装置 ============
_PNG = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                     "01f15c4890000000d49444154789c626001000000ffff030000060005"
                     "57bfabd40000000049454e44ae426082")


def _save_proj(tmp_path):
    """s01 三镜（c01/c02 有首帧候选，c03 无任何生成记录）+ 角色「阿派」带 front 视图。"""
    frames = tmp_path / "output" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    f1 = frames / "s01_c01_first_v1.png"
    f1.write_bytes(_PNG)
    f2 = frames / "s01_c02_first_v1.png"
    f2.write_bytes(_PNG)
    chars = tmp_path / "output" / "characters"
    chars.mkdir(parents=True, exist_ok=True)
    fv = chars / "阿派_front.png"
    fv.write_bytes(_PNG)

    def _batch(path: Path):
        return ImageBatch(batch_id=f"b_{path.stem}", kind="first_frame",
                          status=ShotStatus.DONE.value,
                          candidates=[Candidate(candidate_id=f"cand_{path.stem}",
                                                local=str(path),
                                                url="https://fake.agnes/orig.png",
                                                status=ShotStatus.DONE.value)])

    s1 = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="机甲在机库中跪姿休眠",
              characters=["阿派"], shot_size="wide shot", camera="平视",
              opening_state="目镜尚未点亮", dialogue="系统启动中")
    s1.first_frame_batches = [_batch(f1)]
    s1.selected_first_frame = f"cand_{f1.stem}"
    s2 = Shot(shot_id="s01_c02", scene_id="s01", index=2, description="机甲站起",
              characters=["阿派"], shot_size="medium shot")
    s2.first_frame_batches = [_batch(f2)]
    s2.selected_first_frame = f"cand_{f2.stem}"
    s3 = Shot(shot_id="s01_c03", scene_id="s01", index=3, description="机甲走出机库")
    proj = Project(name=PROJECT, style=StyleAnchor(), story_text="剧本正文。",
                   characters=[Character(name="阿派", description_en="a heavy mecha, amber visor",
                                         views={"front": Asset(local=str(fv), status=ShotStatus.DONE.value)})],
                   scenes=[Scene(scene_id="s01", summary="机甲苏醒", characters=["阿派"],
                                 shots=[s1, s2, s3])],
                   next_scene_seq=2)
    ProjectStore(tmp_path).save(proj)


def _runtime(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _call(rt, tmp_path, **kw):
    args = {"shot_id": "s01_c01"}
    args.update(kw.pop("args", {}))
    return _execute_visual_review(rt, PROJECT, json.dumps(args, ensure_ascii=False),
                                  "instr", "m1", **kw)


# ============ 1. 工具注册与边界 ============
def test_tool_registered():
    names = {t["function"]["name"] for t in DIRECTOR_TOOLS}
    assert "visual.review_image" in names
    assert VISION_REVIEW_TOOL["function"]["name"] == "visual.review_image"
    assert TOOL_EXECUTORS["visual.review_image"] is _execute_visual_review
    assert _tool_label("visual.review_image") == "视觉审查"


def test_bad_arguments_and_not_found(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeVisionClient([GOOD_REVIEW]))
    with pytest.raises(AgentError) as ei:
        _run(_execute_visual_review(rt, PROJECT, json.dumps({}), "i", "m"))
    assert ei.value.code == "bad_arguments"
    with pytest.raises(AgentError) as ei:
        _run(_call(rt, tmp_path, args={"shot_id": "s01_c01", "focus": ["nope"]}))
    assert ei.value.code == "bad_arguments" and "shot_adherence" in ei.value.message
    with pytest.raises(AgentError) as ei:
        _run(_call(rt, tmp_path, args={"shot_id": "s01_c01", "frame": "middle"}))
    assert ei.value.code == "bad_arguments"
    with pytest.raises(AgentError) as ei:
        _run(_call(rt, tmp_path, args={"shot_id": "s09_c09"}))
    assert ei.value.code == "not_found"


# ============ 2. 基础审查路径（T9 read-only + telemetry） ============
def test_review_basic_receipt_context_telemetry(tmp_path):
    _save_proj(tmp_path)
    fake = FakeVisionClient([GOOD_REVIEW])
    rt = _runtime(tmp_path, client=fake)

    before_proj = (tmp_path / "output" / "project.json").read_bytes()
    before_tree = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                   for p in tmp_path.rglob("*") if p.is_file()}

    obs = _run(_call(rt, tmp_path))

    # 回执（T9：只读审查的回执不含 canonical 变更语义）
    assert obs["ok"] and obs["operation"] == "visual.review_image"
    assert obs["shot_id"] == "s01_c01"
    assert obs["review"]["verdict"] == "needs_fix"
    assert obs["review"]["issues"][0]["category"] == "shot_adherence"
    assert obs["reviewed_refs"]["shot_id"] == "s01_c01"
    assert obs["reviewed_refs"]["images"][0]["role"] == "shot"
    tel = obs["telemetry"]
    assert tel["image_count"] == 1 and tel["repairs"] == 0
    assert tel["image_tokens"] == 943 and tel["total_tokens"] == 1380
    assert tel["latency_s"] >= 0 and tel["model"].endswith("agnes-2.5-flash")

    # 上下文构建（§十）：canonical 要求 + 严格 minimal 结构
    vc = fake.vision_calls[0]
    assert vc["model"] == "agnes-2.5-flash" and vc["temperature"] == 0.2
    msgs = vc["messages"]
    assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
    content = msgs[1]["content"]
    assert content[0]["type"] == "text"
    text = content[0]["text"]
    assert "机甲在机库中跪姿休眠" in text and "目镜尚未点亮" in text
    assert "系统启动中" in text and "不应被画成画面内文字" in text
    assert "a heavy mecha, amber visor" not in text      # 未要求 character_consistency 不带角色设定
    img = content[1]
    assert img["type"] == "image_url"
    assert img["image_url"]["url"].startswith("data:image/png;base64,")

    # T9：canonical 逐字节不变 + 除遥测外文件树零变化
    assert (tmp_path / "output" / "project.json").read_bytes() == before_proj
    after_tree = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                  for p in tmp_path.rglob("*") if p.is_file()}
    for name, data in before_tree.items():
        assert after_tree.get(name) == data, f"文件被改动: {name}"
    new_files = set(after_tree) - set(before_tree)
    assert new_files <= {"output/agents/director/visual_reviews.jsonl"}

    # 遥测 jsonl（非 canonical 观测工件）
    lines = (tmp_path / "output" / "agents" / "director" /
             "visual_reviews.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    assert rec["shot_id"] == "s01_c01" and rec["review"]["verdict"] == "needs_fix"
    assert rec["telemetry"]["image_count"] == 1


# ============ 3. 资产解析（T6 不可访问 → 诚实失败 / URL 退回） ============
def test_no_image_honest_failure(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeVisionClient([GOOD_REVIEW]))
    with pytest.raises(AgentError) as ei:
        _run(_execute_visual_review(rt, PROJECT, json.dumps({"shot_id": "s01_c03"}),
                                    "i", "m"))
    assert ei.value.code == "no_image" and "s01_c03" in ei.value.message


def test_url_only_candidate_used(tmp_path):
    """本地文件缺失但有原始 URL → 退回 URL 模式（不假失败）。"""
    _save_proj(tmp_path)
    ProjectStore(tmp_path).update_shot(
        "s01_c01", lambda s: setattr(s.first_frame_batches[0].candidates[0],
                                     "local", str(tmp_path / "nope.png")))
    fake = FakeVisionClient([GOOD_REVIEW])
    rt = _runtime(tmp_path, client=fake)
    obs = _run(_call(rt, tmp_path))
    assert obs["ok"]
    img = fake.vision_calls[0]["messages"][1]["content"][1]
    assert img["image_url"]["url"] == "https://fake.agnes/orig.png"
    assert obs["reviewed_refs"]["images"][0]["source"] == "url"


# ============ 4. focus：character_consistency / continuity（context builder） ============
def test_focus_char_ref_and_continuity(tmp_path):
    _save_proj(tmp_path)
    fake = FakeVisionClient([GOOD_REVIEW])
    rt = _runtime(tmp_path, client=fake)
    obs = _run(_call(rt, tmp_path, args={
        "shot_id": "s01_c02",
        "focus": ["shot_adherence", "character_consistency", "continuity"]}))
    assert obs["ok"]
    content = fake.vision_calls[0]["messages"][1]["content"]
    text, n_images = content[0]["text"], len(content) - 1
    assert n_images == 3                     # 本镜 + 角色参考 + 邻镜
    assert "阿派：a heavy mecha, amber visor" in text
    assert "图1=本镜画面" in text and "角色参考图（阿派）" in text
    assert "邻镜 s01_c01" in text
    roles = [m["role"] for m in obs["reviewed_refs"]["images"]]
    assert roles == ["shot", "char_ref", "prev_shot"]
    assert obs["reviewed_refs"]["images"][1]["view"] == "front"


def test_focus_char_without_views_honest_uncertain_hint(tmp_path):
    """角色没有三视图 → 明示参考不可用并提示 uncertain，不凭空判断一致性。"""
    _save_proj(tmp_path)
    proj = ProjectStore(tmp_path).load()
    proj.characters[0].views = {}
    ProjectStore(tmp_path).save(proj)
    fake = FakeVisionClient([GOOD_REVIEW])
    rt = _runtime(tmp_path, client=fake)
    obs = _run(_call(rt, tmp_path, args={
        "shot_id": "s01_c01", "focus": ["character_consistency"]}))
    assert obs["ok"]
    text = fake.vision_calls[0]["messages"][1]["content"][0]["text"]
    assert "参考图不可用" in text and "uncertain" in text


# ============ 5. 结构化输出（T5：strict parse + 一次 repair + 明确失败） ============
def test_parse_repair_once(tmp_path):
    _save_proj(tmp_path)
    fake = FakeVisionClient([BAD_REVIEW, GOOD_REVIEW])
    rt = _runtime(tmp_path, client=fake)
    obs = _run(_call(rt, tmp_path))
    assert obs["ok"] and obs["review"]["verdict"] == "needs_fix"
    assert obs["telemetry"]["repairs"] == 1
    assert len(fake.vision_calls) == 2
    repair_msgs = fake.vision_calls[1]["messages"]
    assert repair_msgs[-2]["role"] == "assistant" and repair_msgs[-1]["role"] == "user"
    assert "JSON" in repair_msgs[-1]["content"]


def test_parse_fail_twice_honest(tmp_path):
    """两次都不合法 → review_parse_failed（绝不返回假结论）。"""
    _save_proj(tmp_path)
    fake = FakeVisionClient([BAD_REVIEW, BAD_REVIEW])
    rt = _runtime(tmp_path, client=fake)
    with pytest.raises(AgentError) as ei:
        _run(_call(rt, tmp_path))
    assert ei.value.code == "review_parse_failed"


def test_invalid_fields_trigger_repair(tmp_path):
    _save_proj(tmp_path)
    bad = json.dumps({"verdict": "good", "severity": "major", "summary": "s", "issues": []},
                     ensure_ascii=False)
    fake = FakeVisionClient([bad, GOOD_REVIEW])
    rt = _runtime(tmp_path, client=fake)
    obs = _run(_call(rt, tmp_path))
    assert obs["ok"] and obs["telemetry"]["repairs"] == 1
    assert len(fake.vision_calls) == 2


# ============ 6. provider 错误与未配置（T8：诚实失败） ============
def test_provider_error_translated(tmp_path):
    _save_proj(tmp_path)
    fake = FakeVisionClient(vision_error=AgnesError("重试 5 次仍失败: 429", 429, "body"))
    rt = _runtime(tmp_path, client=fake)
    with pytest.raises(AgentError) as ei:
        _run(_call(rt, tmp_path))
    assert ei.value.code == "provider_error" and "429" in ei.value.message


def test_client_without_vision_not_configured(tmp_path):
    _save_proj(tmp_path)

    class _PlainClient:
        pass

    rt = _runtime(tmp_path, client=_PlainClient())
    with pytest.raises(AgentError) as ei:
        _run(_call(rt, tmp_path))
    assert ei.value.code == "not_configured"


# ============ 7. 回执渲染 / trace ============
def test_op_human_and_trace_target():
    r = {"ok": True, "operation": "visual.review_image", "shot_id": "s05_c03",
         "review": {"verdict": "needs_fix",
                    "issues": [{"category": "shot_adherence", "severity": "major",
                                "observation": "x"}]}}
    assert _op_human(r) == "视觉审查 s05_c03（needs_fix，1 条问题）"
    t = _trace_entry(1, "visual.review_image", "{}", r)
    assert t["tool"] == "visual.review_image" and t["target"] == "s05_c03" and t["ok"]


# ============ 8. Director turn 全链路（T7：用户要求查图 → 真实调用工具） ============
def test_director_loop_calls_visual_review(tmp_path):
    _save_proj(tmp_path)
    fake = FakeVisionClient([GOOD_REVIEW])
    fake.chat_script = [
        (None, [{"id": "c1", "type": "function",
                 "function": {"name": "visual.review_image",
                              "arguments": json.dumps({"shot_id": "s01_c01"},
                                                      ensure_ascii=False)}}]),
        ("审查结论：needs_fix——目镜已点亮，与要求的起始状态不符。", None),
    ]
    rt = _runtime(tmp_path, client=fake)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "检查这张生成图是否符合当前镜头要求"))

    assert len(fake.vision_calls) == 1, "Director 必须真实发起视觉审查调用"
    # 只读工具回执不进 receipts（写域账本），但 trace 如实记录（N2 收口同口径）
    tr = [t for t in res["trace"] if t["tool"] == "visual.review_image"]
    assert tr and tr[0]["target"] == "s01_c01" and tr[0]["ok"]
    assert res.get("status") is None        # 真实工具支撑的结论不触发假成功闸门
