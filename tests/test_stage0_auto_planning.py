# tests/test_stage0_auto_planning.py
"""PR B1：Stage 0 自主规划全链测试（GPT 契约 T23–T28 / T33 / T34 的 B1 子集）。

全部 mock：内存 FakeIndex（CAS）+ FakeChat（脚本化 LLM）；零网络、零媒体。
覆盖：
- T23 auto+约120s → approximate 任务身份 → 规划 → 冻结（指纹非空）
- T23b 区间连接符回归：到/至/-/~/～(U+FF5E)/〜(U+301C) 全部解析成 range
- T23c 精确标记作用域：别句的「必须」不得把软目标时长升格成 exact
- T24 auto+无时长 → unspecified → 默认内部窗口仍产出可冻结蓝图
- T25 同 nonce 换任务 → Stage0Conflict（身份拒绝）
- T26 文本预留先于网络；调用失败额度不回退（含失败那次）
- T27 冻结前崩溃 → 重新规划允许（同任务幂等 bootstrap）
- T28 冻结后蓝图被篡改 → verify() fail-closed
- T33 skill 纪律真实进入系统提示（非 grep：FakeChat 捕获的 prompt 断言）
- T34 非 create 意图 → NotYetWired 且零 LLM 调用
- 集成：_run_auto_acceptance 在 fake 注入下走到 B1 边界终止（零媒体断言）
"""
import asyncio
import json
from pathlib import Path

import pytest

from src.ai.producer_planning_models import TaskSpec
from src.ai.producer_planning_service import (
    NotYetWired, ProducerPlanningService, allocate_scene_budgets,
    effective_planning_window, extract_duration_intent, load_skill_discipline,
)
from tools.stage0_state import Stage0Conflict, Stage0Corrupt, Stage0Record


# ============ 内存 Fake（CAS 索引 + LLM client） ============

class FakeIndex:
    """GitHubContentsIndex 的内存替身：read / write_cas（blob_sha 乐观并发）。"""

    def __init__(self):
        self.blob = None  # {"doc": ..., "blob_sha": ...}
        self.writes = 0

    def read(self):
        return self.blob

    def write_cas(self, candidate, expected_sha, message=""):
        current = self.blob
        if expected_sha is not None and (current is None or current["blob_sha"] != expected_sha):
            return {"ok": False}
        if current is not None and expected_sha is None:
            return {"ok": False}
        self.writes += 1
        sha = f"fake-{self.writes:04d}"
        self.blob = {"doc": candidate, "blob_sha": sha}
        return {"ok": True, "new_sha": sha}


class FakeChat:
    """脚本化 LLM：按队列返回；可注入故障；捕获 system prompt。

    reserve_probe：每次 chat 调用时读取的当前预留计数（断言"预留先于请求"）。
    """

    def __init__(self, replies=None, fail_at=None):
        self.replies = list(replies or [])
        self.fail_at = fail_at  # 第 N 次（1 起）调用抛网络错
        self.calls = 0
        self.captured = []      # (system, user, reserved_at_call)
        self.reserve_probe = None  # 外部注入：返回当前 text_calls_used

    async def chat(self, messages, **kw):
        """与真实 AgnesClient.chat 同签名：消息列表 → 文本。"""
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        self.calls += 1
        reserved = self.reserve_probe() if self.reserve_probe else -1
        self.captured.append((system, user, reserved))
        if self.fail_at is not None and self.calls == self.fail_at:
            raise OSError("simulated network outage")
        if not self.replies:
            raise AssertionError("FakeChat exhausted")
        return self.replies.pop(0)


def _blueprint_reply(scenes=None, beats=None):
    """一份 LLM 会返回的合法规划 JSON（beats 在每场内，与 _parse_blueprint 契约一致）。"""
    default_scenes = [
        {"title": "起飞", "summary": "热气球在地面充气，乘客登舱，黎明起飞。", "beats": 2},
        {"title": "云海", "summary": "热气球升入云层之上，穿越翻涌云海。", "beats": 3},
        {"title": "抵达", "summary": "热气球越过山脊，缓缓降落在山谷。", "beats": 2},
    ]
    if beats is not None:
        for s, b in zip(default_scenes, beats):
            s["beats"] = b
    payload = {
        "story_text": "一只热气球在黎明起飞，穿越云海抵达山谷，途中经历风的考验。",
        "scenes": scenes or default_scenes,
        "style": {"art_style": "photorealistic cinematic adventure",
                  "palette": "dawn gold, cloud white", "aspect_ratio": "16:9"},
    }
    return json.dumps(payload, ensure_ascii=False)


async def _plan_with(fakes=None, task_spec=None, window_limits=None):
    fakes = fakes or {}
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    task = task_spec or TaskSpec(
        intent="create", request_text="拍一个热气球穿越云海的故事，大概两分钟",
        duration=extract_duration_intent("拍一个热气球穿越云海的故事，大概两分钟"))
    stage0.bootstrap(task)
    chat = fakes.get("chat") or FakeChat([_blueprint_reply()])
    chat.reserve_probe = lambda: stage0.text_calls_used
    service = ProducerPlanningService(chat, stage0)
    limits = window_limits or {"text_calls": 10, "image_calls": 5, "video_seconds": 170}
    result = await service.plan(task, limits)
    return stage0, chat, result


# ============ T23：auto + 约120s ============

def test_t23_duration_extraction_approximate_120():
    d = extract_duration_intent("拍一个热气球穿越云海的故事，大概两分钟")
    assert d.mode == "approximate" and d.target_seconds == 120


def test_t23_plan_and_freeze():
    stage0, chat, result = asyncio.run(_plan_with())
    assert chat.calls >= 1
    assert stage0.plan_fingerprint is None  # service 只出蓝图；冻结由主链做
    fingerprint = stage0.freeze(json.loads(json.dumps(result.canonical_payload())))
    assert fingerprint and len(fingerprint) == 64
    assert stage0.doc["status"] == "FROZEN"
    assert stage0.text_calls_used == chat.calls  # 每次调用都预留
    window = effective_planning_window(
        extract_duration_intent("拍一个热气球穿越云海的故事，大概两分钟"), {})
    assert window[0] <= sum(b.target_s for b in result.scene_budgets) <= window[1]


# ============ T23b：区间连接符回归（到/至/-/~/～） ============

@pytest.mark.parametrize("text,expected", [
    ("拍一个45到75秒的无人机短片", (45, 75)),
    ("拍一个45至75秒的无人机短片", (45, 75)),
    ("拍一个45-75秒的无人机短片", (45, 75)),
    ("拍一个45~75秒的无人机短片", (45, 75)),
    ("拍一个45～75秒的无人机短片", (45, 75)),
    ("拍一个45〜75秒的无人机短片", (45, 75)),
])
def test_t23b_duration_range_connectors(text, expected):
    """每种区间连接符都必须解析成 range，不能退化成 approximate。"""
    d = extract_duration_intent(text)
    assert d.mode == "range"
    assert tuple(d.range_seconds) == expected


# ============ T23c：精确标记只修饰同一分句的时长 ============

@pytest.mark.parametrize("text,mode,target", [
    ("必须恰好60秒的无人机短片", "exact", 60),
    ("拍一个大约一分钟的无人机短片", "approximate", 60),
    ("制作大约一分钟的新续集。必须直接复用旧片里的一个镜头。",
     "approximate", 60),
    ("制作一分钟的新续集。必须直接复用旧片里的一个镜头。",
     "approximate", 60),
])
def test_t23c_exact_marker_scope(text, mode, target):
    """别句的「必须」不得把软目标时长升格成 exact。"""
    d = extract_duration_intent(text)
    assert d.mode == mode
    assert d.target_seconds == target


# ============ T24：无时长 ============

def test_t24_unspecified_uses_default_window():
    d = extract_duration_intent("做个卫星环绕地球的视频")
    assert d.mode == "unspecified"
    assert effective_planning_window(d, {}) == (60, 180)


def test_t24_plan_without_duration_freezes():
    task = TaskSpec(intent="create", request_text="做个卫星环绕地球的视频",
                    duration=extract_duration_intent("做个卫星环绕地球的视频"))
    stage0, chat, result = asyncio.run(_plan_with(task_spec=task))
    assert stage0.freeze(json.loads(json.dumps(result.canonical_payload())))
    assert 60 <= sum(b.target_s for b in result.scene_budgets) <= 180


# ============ T25：同 nonce 换任务 ============

def test_t25_identity_conflict_on_different_task():
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    stage0.bootstrap(TaskSpec(intent="create", request_text="热气球两分钟"))
    with pytest.raises(Stage0Conflict):
        stage0.bootstrap(TaskSpec(intent="create", request_text="换一个任务，还是两分钟"))


def test_t25_same_task_bootstrap_idempotent():
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    task = TaskSpec(intent="create", request_text="热气球两分钟")
    stage0.bootstrap(task)
    stage0.bootstrap(task)  # 同任务幂等
    assert stage0.doc["task_spec_sha256"] == task.sha256()


# ============ T26：文本预留先于请求；失败不回退 ============

def test_t26_reserve_precedes_network_and_survives_failure():
    chat = FakeChat([_blueprint_reply()], fail_at=1)
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    task = TaskSpec(intent="create", request_text="热气球，大概两分钟",
                    duration=extract_duration_intent("热气球，大概两分钟"))
    stage0.bootstrap(task)
    chat.reserve_probe = lambda: stage0.text_calls_used
    service = ProducerPlanningService(chat, stage0)
    with pytest.raises(OSError):
        asyncio.run(service.plan(task, {"text_calls": 10, "image_calls": 5,
                                        "video_seconds": 170}))
    assert stage0.text_calls_used == 1  # 失败那次已预留，不回退
    for system, user, reserved in chat.captured:
        assert reserved >= 1  # 预留发生在请求发出前


def test_t26_retry_after_failure_uses_new_reservation():
    chat = FakeChat([_blueprint_reply()], fail_at=1)
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    task = TaskSpec(intent="create", request_text="热气球两分钟",
                    duration=extract_duration_intent("热气球两分钟"))
    stage0.bootstrap(task)
    chat.reserve_probe = lambda: stage0.text_calls_used
    service = ProducerPlanningService(chat, stage0)
    with pytest.raises(OSError):
        asyncio.run(service.plan(task, {"text_calls": 10, "image_calls": 5,
                                        "video_seconds": 170}))
    result = asyncio.run(service.plan(task, {"text_calls": 10, "image_calls": 5,
                                             "video_seconds": 170}))
    assert stage0.text_calls_used == 2
    assert result.plan_sha256()


# ============ T27：冻结前崩溃 → 允许重规划 ============

def test_t27_prefreeze_replan_allowed():
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    task = TaskSpec(intent="create", request_text="热气球两分钟")
    stage0.bootstrap(task)
    stage0.mark_planning()
    stage0.reserve_text_call()  # 模拟第一次规划已消耗一次
    assert stage0.plan_fingerprint is None  # 未冻结
    # 崩溃后重入：bootstrap 幂等 → 可重新规划
    stage0.bootstrap(task)
    assert stage0.text_calls_used == 1  # 额度延续，不回退


# ============ T28：冻结后蓝图被篡改 → fail-closed ============

def test_t28_tampered_blueprint_fail_closed():
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    task = TaskSpec(intent="create", request_text="热气球两分钟")
    stage0.bootstrap(task)
    stage0.freeze({"story_text": "原蓝图", "scenes": [], "scene_budgets": [],
                   "execution_bounds": {"text_calls": 10}})
    # 外部篡改：改内容、保指纹
    idx.blob["doc"]["blueprint"]["story_text"] = "被篡改的蓝图"
    with pytest.raises(Stage0Corrupt):
        stage0.verify()


def test_t28_frozen_refuses_replanning():
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    stage0.bootstrap(TaskSpec(intent="create", request_text="热气球两分钟"))
    stage0.freeze({"story_text": "x", "scenes": [], "scene_budgets": [],
                   "execution_bounds": {"text_calls": 10}})
    with pytest.raises(Stage0Conflict):
        stage0.freeze({"story_text": "y", "scenes": [], "scene_budgets": [],
                       "execution_bounds": {"text_calls": 10}})


# ============ T33：skill 纪律真实进入系统提示 ============

def test_t33_skill_discipline_injected_into_prompt():
    stage0, chat, _ = asyncio.run(_plan_with())
    system = chat.captured[0][0]
    # 来自 SKILL.md「执行纪律」段的真实内容（非 grep 文件，是运行时注入）
    assert "先辨别意图再规划" in system
    assert "付费媒体前必须冻结" in system or "冻结有限执行计划" in system
    # 来自 references/producer.md 的角色指令
    assert "coordinator" in system or "只读" in system


def test_t33_skill_loading_fail_closed(tmp_path):
    empty = tmp_path / "not-a-skill"
    empty.mkdir()
    with pytest.raises(RuntimeError):
        load_skill_discipline(empty)


# ============ T34：非 create 意图零调用拒绝 ============

def test_t34_continuation_intent_rejected_without_llm():
    chat = FakeChat()
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-auto")
    # "继续…补齐" → add_assets（suggest_intent 分流）
    task = TaskSpec(intent="add_assets",
                    request_text="把深海项目没做完的素材继续生成补齐",
                    source_project="deepsea")
    stage0.bootstrap(task)
    chat.reserve_probe = lambda: stage0.text_calls_used
    service = ProducerPlanningService(chat, stage0)
    with pytest.raises(NotYetWired):
        asyncio.run(service.plan(task, {"text_calls": 10, "image_calls": 5,
                                        "video_seconds": 100}))
    assert chat.calls == 0  # 零 LLM 调用
    assert stage0.text_calls_used == 0  # 零预留（无任何付费前动作）


def test_t34_normalize_routes_continuation_and_c2(monkeypatch):
    """C2: sequel and extension inputs are identified; missing source fails."""
    from tools import full_project_cloud_acceptance as acc
    env = {"PRODUCTION_REQUEST": "继续上次深海项目，把没做完的做完成",
           "SOURCE_PROJECT": "deepsea"}
    spec = acc.normalize_dispatch_input_to_task_spec("unit", environ=env)
    assert spec.intent == "complete_project" and spec.source_project == "deepsea"
    sequel = acc.normalize_dispatch_input_to_task_spec(
        "unit", environ={"PRODUCTION_REQUEST": "给这条片子拍一个新续集",
                         "SOURCE_PROJECT": "deepsea"})
    assert sequel.intent == "sequel" and sequel.source_project == "deepsea"
    with pytest.raises(ValueError, match="source_project"):
        acc.normalize_dispatch_input_to_task_spec(
            "unit", environ={"PRODUCTION_REQUEST": "给这条片子拍一个新续集"})
    env2 = {"PRODUCTION_REQUEST": "拍个卫星环绕地球的视频"}
    spec = acc.normalize_dispatch_input_to_task_spec("unit", environ=env2)
    assert spec.intent == "create"


def test_t34_normalize_requires_production_request():
    from tools import full_project_cloud_acceptance as acc
    with pytest.raises(ValueError):
        acc.normalize_dispatch_input_to_task_spec("unit", environ={})


# ============ 分配器契约（T23/T24 的数值面） ============

def test_allocator_sums_inside_window():
    for beats, w in [([2, 3, 1, 3], (102, 138)), ([1, 1, 1], (48, 72)),
                     ([5], (100, 140)), ([1, 2, 3, 4, 5], (200, 240))]:
        bs = allocate_scene_budgets(beats, w)
        assert w[0] <= sum(b.target_s for b in bs) <= w[1], (beats, w)
        for b in bs:
            assert b.shot_min * 4 <= b.max_s + 4  # 可行性（弹性兜底后）
            assert b.shot_max * 11 >= b.min_s


# ============ 集成：_run_auto_acceptance 全链（fake 注入，零媒体） ============

class FakeProjectManager:
    """必须严格镜像 src.utils.project_manager.ProjectManager 的契约。

    真实实现 get_project_dir() 在目录不存在时抛 FileNotFoundError，
    create_project() 在目录已存在时抛 FileExistsError 且返回项目目录。
    假实现若比真实实现宽松，就会悄悄掩盖「先取目录后创建」这类顺序缺陷
    （第 06 轮实测：真实云端长片在物化这一步崩溃，而 3684 个测试全绿）。
    """

    def __init__(self, base):
        self.base = Path(base)

    def get_project_dir(self, name):
        d = self.base / name
        if not d.exists():
            raise FileNotFoundError(f"项目不存在: {name}")
        return d

    def create_project(self, name):
        d = self.base / name
        if d.exists():
            raise FileExistsError(f"项目已存在: {name}")
        (d / "input").mkdir(parents=True)
        (d / "output" / "videos").mkdir(parents=True)
        return d


async def _noop_start():
    return None


def test_integration_auto_acceptance_full_chain(monkeypatch, tmp_path):
    """auto 全链（PR B2）：身份→规划→冻结→物化(+sidecar)→绑定→返回生产上下文。

    零媒体断言：FakeChat 不实现 generate_image/create_video；生产段由固定档
    既有测试覆盖（共享代码），本测试到 _run_auto_acceptance 返回为止。
    二次进入（模拟续跳）：不得重调 LLM，蓝图原样恢复，digest 稳定，且不需要
    PRODUCTION_REQUEST（身份权威在 B1 stage0 记录）。
    """
    from tools import video_task_state as vts
    from tools import e2e_autonomous_run as e2e
    from tools import full_project_cloud_acceptance as acc
    import deps

    files = {}  # filename -> FakeIndex（同 nonce 两个身份域）

    def fake_task_index(token, nonce, filename="state.json", transport=None):
        return files.setdefault(filename, FakeIndex())

    monkeypatch.setattr(vts, "task_index", fake_task_index)
    monkeypatch.setattr(e2e, "ensure_branch", lambda idx: None)
    monkeypatch.setattr(acc, "_ensure_app_started", _noop_start)
    chat = FakeChat(replies=[_blueprint_reply()])
    monkeypatch.setattr(deps, "agnes_client", chat)
    pm = FakeProjectManager(tmp_path / "projects")
    monkeypatch.setattr(deps, "project_manager", pm)

    env = {"PRODUCTION_REQUEST": "拍一个热气球穿越云海的故事，大概两分钟",
           "FILM_TARGET_SECONDS": "auto"}
    monkeypatch.setattr(acc.os, "environ", env)
    summary, out = {}, tmp_path / "out"
    out.mkdir()

    project_name, dyn_profile, cp, restored, previous = asyncio.run(
        acc._run_auto_acceptance("film-auto-int", out, summary, "tok"))

    # 身份/冻结/绑定链
    assert project_name == "cloud3min_film-auto-int"
    assert summary["stage0_status"] == "FROZEN"
    assert summary["plan_fingerprint"]
    assert summary["execution_request_sha256"]
    assert restored is None and previous is not None
    # 动态 profile（PR B2）：字段齐全且来自蓝图
    assert dyn_profile["scene_count"] == 3
    assert dyn_profile["planned_min"] >= 102 and dyn_profile["planned_max"] <= 138
    assert dyn_profile["budget"] == 1_000_000_000 and dyn_profile["min_final"] >= 102
    assert chat.calls == 1
    # sidecar 已写入（Director 注入的依据）
    sidecar = (tmp_path / "projects" / "cloud3min_film-auto-int"
               / "output" / "ai" / "scene_budgets.json")
    assert sidecar.is_file()
    import json as _json
    sc = _json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["plan_fingerprint"] == summary["plan_fingerprint"]
    assert len(sc["scene_budgets"]) == 3
    # 生产检查点已绑定且 digest 与蓝图一一对应
    prod = files["full-project-production.json"].blob["doc"]
    assert prod["phase"] == "STAGE0_BOUND"
    assert prod["film_target_seconds"] == "auto"
    assert prod["plan_fingerprint"] == summary["plan_fingerprint"]
    assert prod["request_sha256"] == summary["execution_request_sha256"]
    assert (tmp_path / "projects/cloud3min_film-auto-int/output/project.json").is_file()

    # ---- 二次进入（模拟续跳恢复，无 PRODUCTION_REQUEST 环境）----
    chat2 = FakeChat(replies=[])  # 空队列：任何调用都会 AssertionError
    monkeypatch.setattr(deps, "agnes_client", chat2)
    env2 = {"FILM_TARGET_SECONDS": "auto"}  # 无任务文本（身份权威在 B1 记录）
    monkeypatch.setattr(acc.os, "environ", env2)
    summary2 = {}
    name2, dyn2, cp2, restored2, previous2 = asyncio.run(
        acc._run_auto_acceptance("film-auto-int", out, summary2, "tok"))
    assert name2 == project_name
    assert summary2["stage0_status"] == "FROZEN_RESTORED"
    assert summary2["plan_fingerprint"] == summary["plan_fingerprint"]
    assert summary2["execution_request_sha256"] == summary["execution_request_sha256"]
    assert dyn2 == dyn_profile  # 动态 profile 由冻结蓝图导出，跨 hop 稳定
    assert chat2.calls == 0  # 蓝图恢复零 LLM 调用
    assert summary2["stage0_text_calls_used"] == summary["stage0_text_calls_used"]


# ============ 第 06 轮回归：物化顺序（假实现必须与真实实现同契约） ============

def test_fake_project_manager_mirrors_real_contract(tmp_path):
    """假实现与真实 ProjectManager 的契约必须一致。

    真实实现 src/utils/project_manager.py:get_project_dir 在目录不存在时抛
    FileNotFoundError；create_project 在目录已存在时抛 FileExistsError 并返回
    项目目录。假实现若比真实实现宽松，就会悄悄掩盖顺序缺陷。
    """
    from src.utils.project_manager import ProjectManager
    real = ProjectManager(tmp_path / "real")
    fake = FakeProjectManager(tmp_path / "fake")

    for pm in (real, fake):
        with pytest.raises(FileNotFoundError):
            pm.get_project_dir("cloud3min_missing")

    real_dir = real.create_project("cloud3min_x")
    fake_dir = fake.create_project("cloud3min_x")
    assert real_dir == real.get_project_dir("cloud3min_x")
    assert fake_dir == fake.get_project_dir("cloud3min_x")
    for pm in (real, fake):
        with pytest.raises(FileExistsError):
            pm.create_project("cloud3min_x")  # 已存在绝不覆盖


def test_auto_materialization_on_fresh_hop_writes_blueprint(monkeypatch, tmp_path):
    """第 06 轮实测回归：auto + create 意图在全新 hop 上必须真正建出项目。

    实测故障：A 仓真实云端长片 run #39（2026-10-29）在物化阶段崩溃 ——
    FileNotFoundError: 项目不存在: cloud3min_film3m-40792621748。
    根因：先调用 get_project_dir()（要求目录已存在）后调用 create_project()，
    而全新 hop 的项目目录根本不存在 ⇒ 永远走不到创建那一步。
    本用例用与真实实现同契约的假实现把该顺序缺陷钉死。
    """
    from tools import video_task_state as vts
    from tools import e2e_autonomous_run as e2e
    from tools import full_project_cloud_acceptance as acc
    import deps

    files = {}

    def fake_task_index(token, nonce, filename="state.json", transport=None):
        return files.setdefault(filename, FakeIndex())

    monkeypatch.setattr(vts, "task_index", fake_task_index)
    monkeypatch.setattr(e2e, "ensure_branch", lambda idx: None)
    monkeypatch.setattr(acc, "_ensure_app_started", _noop_start)
    monkeypatch.setattr(deps, "agnes_client", FakeChat(replies=[_blueprint_reply()]))
    monkeypatch.setattr(deps, "project_manager",
                        FakeProjectManager(tmp_path / "projects"))
    monkeypatch.setattr(acc.os, "environ", {
        "PRODUCTION_REQUEST": "拍一个热气球穿越云海的故事，大概两分钟",
        "FILM_TARGET_SECONDS": "auto"})

    summary, out = {}, tmp_path / "out"
    out.mkdir()
    project_name, _, _, _, _ = asyncio.run(
        acc._run_auto_acceptance("film-mat-reg", out, summary, "tok"))

    project_json = tmp_path / "projects" / project_name / "output" / "project.json"
    assert project_json.is_file(), "全新 hop 必须建出项目并写入 project.json"
    doc = json.loads(project_json.read_text(encoding="utf-8"))
    # 落盘的必须是冻结蓝图，而不是 create_project 留下的空项目
    assert doc.get("story_text", "").strip(), "蓝图 story_text 必须落盘"
    assert doc.get("scenes"), "蓝图 scenes 必须落盘"


def test_auto_restore_precedes_materialization():
    """恢复必须早于物化（第 06 轮实测：否则自动续跑永远走不通）。

    restore_snapshot 明确拒绝覆盖已存在的项目目录与 cache/jobs.json
    （FileExistsError: recovery cannot overwrite an existing project/job queue）。
    而全新 runner 上物化会先建出项目目录、_ensure_deps_ready() 会先建出
    cache/jobs.json ⇒ 若物化排在恢复之前，恢复必被挡在门外，
    「330 分钟到点自动续跑」这条链在 auto 路径上就断了。

    本用例用源码顺序钉死这条不变量（跨 Actions run 的快照恢复无法在单测里
    真跑，故以顺序断言守住这条最容易被后续重构打破的约束）。
    """
    import inspect
    from tools import full_project_cloud_acceptance as acc
    src = inspect.getsource(acc._run_auto_acceptance)
    i_restore = src.index("restore_snapshot(")
    i_materialize = src.index("create_project(")
    assert i_restore < i_materialize, "恢复必须早于物化，否则会被「拒绝覆盖」守卫挡住"
    assert src.index("ProductionCheckpoint(") < i_restore, "恢复要读检查点，绑定身份必须更早"


# ============ 第 06 轮实测回归：auto 路径的确定性镜头指令 ============
#
# 实测故障：A 仓真实云端长片 run #40（2026-10-29）在 Director 阶段崩溃 ——
#   DIRECTOR_FAILED 故事段 s01 预算校验仍不通过: ['总时长 28.0s 超出本场窗口 32-44s']
#   （首次 21.0s → repair 后 28.0s，两次都低于场景窗口下界 32s）
# 根因：Director 面对**纯叙事摘要**时倾向欠产镜头（T-REAL-A 同型）。
# C2 路径早就在 c2_production.py 里把「本段必须规划 N 个连续镜头…」写进
# scene summary；**auto 路径缺这一步**，于是预算校验永远过不去。
# 修复：规划阶段从冻结预算**确定性派生**镜头指令，写进 summary（freeze 之前）。

#: run #40 的真实冻结预算（evidence/g2_run40846737077_artifacts/
#: e2e-evidence__scene_budgets.json），逐字照抄，作为回归夹具。
_RUN40_SCENE_BUDGETS = [
    {"scene_id": "s01", "min_s": 32, "max_s": 44, "shot_min": 3, "shot_max": 11,
     "target_s": 38, "per_shot_min_s": 4, "per_shot_max_s": 11},
    {"scene_id": "s02", "min_s": 42, "max_s": 58, "shot_min": 4, "shot_max": 14,
     "target_s": 50, "per_shot_min_s": 4, "per_shot_max_s": 11},
    {"scene_id": "s03", "min_s": 42, "max_s": 58, "shot_min": 4, "shot_max": 14,
     "target_s": 50, "per_shot_min_s": 4, "per_shot_max_s": 11},
    {"scene_id": "s04", "min_s": 32, "max_s": 44, "shot_min": 3, "shot_max": 11,
     "target_s": 38, "per_shot_min_s": 4, "per_shot_max_s": 11},
    {"scene_id": "s05", "min_s": 20, "max_s": 28, "shot_min": 2, "shot_max": 7,
     "target_s": 24, "per_shot_min_s": 4, "per_shot_max_s": 11},
]


def _budgets_from_run40():
    from src.ai.producer_planning_models import SceneBudget
    return [SceneBudget(
        scene_id=b["scene_id"], min_s=b["min_s"], max_s=b["max_s"],
        shot_min=b["shot_min"], shot_max=b["shot_max"], target_s=b["target_s"],
        per_shot_min_s=b["per_shot_min_s"], per_shot_max_s=b["per_shot_max_s"])
        for b in _RUN40_SCENE_BUDGETS]


def test_derive_shot_instructions_covers_run40_s01():
    """run #40 的 s01（窗口 32-44s）必须派生出落在窗口内的镜头指令。"""
    from src.ai.producer_planning_service import derive_shot_instructions
    notes = derive_shot_instructions(_budgets_from_run40())
    assert "s01" in notes, "s01 必须有镜头指令"
    note = notes["s01"]
    assert "必须规划" in note and "个连续镜头" in note
    # 从指令文本反解出镜头数/每镜秒数/总时长，断言三者自洽且落在预算窗口内
    import re
    m = re.search(r"必须规划(\d+)个连续镜头，每镜约(\d+)秒，总计约(\d+)秒", note)
    assert m, "指令必须可被确定性解析：%r" % note
    desired, per, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert total == desired * per, "总时长必须等于镜头数×每镜秒数"
    b = _RUN40_SCENE_BUDGETS[0]
    assert b["shot_min"] <= desired <= b["shot_max"], "镜头数必须落在预算范围"
    assert b["min_s"] <= total <= b["max_s"], "总时长必须落在场景窗口内"
    assert b["per_shot_min_s"] <= per <= b["per_shot_max_s"], "每镜秒数必须落在合同内"


def test_derive_shot_instructions_all_budgets_feasible():
    """所有场次的派生指令都必须落在各自预算窗口内（否则宁可不注入）。"""
    from src.ai.producer_planning_service import derive_shot_instructions
    import re
    notes = derive_shot_instructions(_budgets_from_run40())
    assert len(notes) == len(_RUN40_SCENE_BUDGETS), "每个场次都要有指令"
    for b in _RUN40_SCENE_BUDGETS:
        note = notes[b["scene_id"]]
        m = re.search(r"必须规划(\d+)个连续镜头，每镜约(\d+)秒，总计约(\d+)秒", note)
        assert m, note
        desired, per, total = (int(m.group(i)) for i in (1, 2, 3))
        assert b["shot_min"] <= desired <= b["shot_max"], b["scene_id"]
        assert b["min_s"] <= total <= b["max_s"], b["scene_id"]
        assert b["per_shot_min_s"] <= per <= b["per_shot_max_s"], b["scene_id"]
        assert total == desired * per, b["scene_id"]


def test_derive_shot_instructions_skips_single_shot_and_short():
    """单镜头段（复用镜头的场）与过短段不注入：没有分解余地。"""
    from src.ai.producer_planning_models import SceneBudget
    from src.ai.producer_planning_service import derive_shot_instructions
    budgets = [
        SceneBudget(scene_id="s01", min_s=8, max_s=8, shot_min=1, shot_max=1,
                    target_s=8),                      # 单镜头段
        SceneBudget(scene_id="s02", min_s=4, max_s=10, shot_min=1, shot_max=2,
                    target_s=8),                      # target < 12
    ]
    assert derive_shot_instructions(budgets) == {}


def test_auto_plan_injects_shot_instruction_into_summary():
    """auto(create) 规划出的场景摘要必须带上确定性镜头指令。"""
    import asyncio as _asyncio
    from src.ai.producer_planning_service import ProducerPlanningService
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-shotnote")
    task = TaskSpec(intent="create",
                    request_text="拍一个热气球穿越云海的故事，大概两分钟",
                    duration=extract_duration_intent(
                        "拍一个热气球穿越云海的故事，大概两分钟"))
    stage0.bootstrap(task)
    chat = FakeChat([_blueprint_reply()])
    service = ProducerPlanningService(chat, stage0)
    result = _asyncio.run(service.plan(
        task, {"text_calls": 10, "image_calls": 5, "video_seconds": 170}))
    for sc in result.scenes:
        assert "必须规划" in sc.summary, (
            "场景 %s 的摘要必须带确定性镜头指令：%r" % (sc.scene_id, sc.summary))
        assert "个连续镜头" in sc.summary


def test_auto_plan_injection_is_frozen_in_fingerprint():
    """注入必须发生在 freeze 之前 ⇒ 蓝图指纹覆盖注入后的摘要（恢复逐字一致）。"""
    import asyncio as _asyncio
    from src.ai.producer_planning_service import ProducerPlanningService
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-shotnote-fp")
    task = TaskSpec(intent="create",
                    request_text="拍一个热气球穿越云海的故事，大概两分钟",
                    duration=extract_duration_intent(
                        "拍一个热气球穿越云海的故事，大概两分钟"))
    stage0.bootstrap(task)
    chat = FakeChat([_blueprint_reply()])
    service = ProducerPlanningService(chat, stage0)
    result = _asyncio.run(service.plan(
        task, {"text_calls": 10, "image_calls": 5, "video_seconds": 170}))
    payload = json.loads(json.dumps(result.canonical_payload()))
    fp = stage0.freeze(payload)
    # 冻结后 verify 必须通过（指纹与内容一致）
    stage0.verify()
    assert stage0.plan_fingerprint == fp
    frozen_summary = stage0.doc["blueprint"]["scenes"][0]["summary"]
    assert "必须规划" in frozen_summary, "冻结的蓝图里必须含镜头指令"


def test_c2_intent_does_not_get_double_injection():
    """C2（extend_existing/sequel）自己会注入指令 ⇒ 规划层不得重复注入。"""
    import asyncio as _asyncio
    from src.ai.producer_planning_service import ProducerPlanningService
    idx = FakeIndex()
    stage0 = Stage0Record(idx, "unittest-c2-nodouble")
    task = TaskSpec(intent="create",
                    request_text="拍一个热气球穿越云海的故事，大概两分钟",
                    duration=extract_duration_intent(
                        "拍一个热气球穿越云海的故事，大概两分钟"))
    stage0.bootstrap(task)
    chat = FakeChat([_blueprint_reply()])
    service = ProducerPlanningService(chat, stage0)
    result = _asyncio.run(service.plan(
        task, {"text_calls": 10, "image_calls": 5, "video_seconds": 170}))
    for sc in result.scenes:
        # create 意图只注入一次（一个「必须规划」标记）
        assert sc.summary.count("必须规划") == 1, sc.summary


# ============ daycarry12m run #59 实测回归：预算 repair 次数 ============
#
# 实测故障（run 41221999724，2026-11-01）：Director 首版 115s 超上限 1s →
# repair 过冲到 57s 低于下限 27s → 仅 1 次 repair 即 DIRECTOR_FAILED。
# LLM 单次修复在「窗口窄、镜头多」时震荡是常态（run #40 21s→28s 同型）。
# 修复：预算 repair 循环上限 1 → 3（coverage repair 语义不变，仍 1 次）。
# FullProject 流程太重无法单测起全套，用源码级断言锁住循环结构。

def test_budget_repair_allows_up_to_three_attempts():
    import inspect, re
    from src.ai import full_project_autopilot as fpa
    src = inspect.getsource(fpa)
    # 预算 repair 循环：while + 上限 3（不再是「repair 一次；仍违反 → fail」）
    m = re.search(r"repair_round = 0\s*\n\s*while violations and repair_round < (\d+):", src)
    assert m, "预算 repair 必须是带次数上限的 while 循环"
    assert int(m.group(1)) == 3, f"预算 repair 上限应为 3，实际 {m.group(1)}"
    # 旧的「一次 repair 即 fail」语义必须绝迹
    assert "repair 一次；仍违反 → fail" not in src, "旧单次 repair 语义残留"
    # coverage repair 保持 1 次（§八 语义不变）
    assert "invalid 最多 1 repair" in src, "coverage repair 次数语义被意外改动"
