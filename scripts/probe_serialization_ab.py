"""AI-7D-R · 多写工具调用行为 A/B 测试（不改 Runtime/OCC/串行/工具源码）。

A 组：现状（正式 _build_system）。
B 组：仅 system prompt 末尾加一条软规则（测试注入 monkeypatch _build_system，不改正式代码）：
  "同一次 assistant response 最多调用一个会修改 story 状态的工具；多个互相独立的只读
   工具可以同时调用。写操作执行并看到最新结果/revision 后，再决定下一个写操作。"

每组跑 20 个真实复杂写任务（真 Agnes agnes-2.5-flash，流式路径，thinking=False），记录：
  write_tool_calls_generated / executed / serialization_rejected / llm_calls /
  prompt_tokens / completion_tokens / reasoning_tokens / 总耗时 / 最终是否正确完成。

不动 tool_choice（不加 parallel_tool_calls=false）、不动串行逻辑、不动 OCC。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

SOFT_RULE = "\n\n【写工具调用规则】同一次 assistant response 最多调用一个会修改 story 状态的工具；多个互相独立的只读工具可以同时调用。写操作执行并看到最新结果/revision 后，再决定下一个写操作。"

WRITE_OPS = {"script.update", "story.create", "story.move", "story.update", "story.delete"}


# ---------- 真实 AgnesClient ----------
def make_real_client():
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))["agnes"]
    key = None
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("AGNES_API_KEY") and "=" in s:
            key = s.split("=", 1)[1].strip().strip('"').strip("'")
    assert key, "AGNES_API_KEY 未找到"
    from src.api.agnes_client import AgnesClient
    config = {"base_url": cfg["base_url"], "api_key": key,
              "models": cfg["models"], "proxy": cfg.get("proxy") or None}
    return AgnesClient(config)


class UsageTrackingClient:
    """包装真 AgnesClient：记录每次 chat_completion_stream 的 usage + llm_calls。"""
    def __init__(self, real):
        self.real = real
        self.reset()

    def reset(self):
        self.llm_calls = 0
        self.prompt = 0
        self.completion = 0
        self.reasoning = 0

    async def chat_completion_stream(self, messages, **kw):
        self.llm_calls += 1
        async for ev in self.real.chat_completion_stream(messages, **kw):
            if ev["kind"] == "finish" and ev.get("usage"):
                u = ev["usage"]
                self.prompt += u.get("prompt_tokens", 0) or 0
                self.completion += u.get("completion_tokens", 0) or 0
                self.reasoning += (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
            yield ev

    async def chat_completion(self, messages, **kw):
        # 走不到（event_cb 路径用 stream），但实现以防
        return await self.real.chat_completion(messages, **kw)

    async def chat(self, messages, **kw):
        return await self.real.chat(messages, **kw)


class Collector:
    """event_cb：统计写工具 generated/executed/串行拒绝。"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.write_gen = 0       # 写工具 tool.started 次数
        self.write_exec = 0      # 写工具 ok=True
        self.write_reject = 0    # 写工具 ok=False 且串行拒绝

    def __call__(self, ev):
        t = ev.get("type")
        name = ev.get("name")
        if name not in WRITE_OPS:
            return
        if t == "tool.started":
            self.write_gen += 1
        elif t == "tool.completed":
            if ev.get("ok"):
                self.write_exec += 1
            elif "串行" in (ev.get("summary") or ""):
                self.write_reject += 1


# ---------- 基础项目（6 段） ----------
def build_proj():
    from src.core.models import Project, Scene, Character, StyleAnchor
    return Project(
        name="demo", style=StyleAnchor(),
        story_text="小雨撑伞走在雨夜街道。她发现一只淋湿的小猫，用外套裹起来带回家。回家后给小猫洗澡取名豆豆。第二天在项圈下发现一张车票，开始寻找它的主人老奶奶。",
        characters=[Character(name="LinXia", description_en="a young woman with an umbrella")],
        scenes=[
            Scene(scene_id="s01", summary="雨夜回家路上", characters=["LinXia"], shots=[]),
            Scene(scene_id="s02", summary="发现淋湿的小猫", characters=["LinXia"], shots=[]),
            Scene(scene_id="s03", summary="用外套包裹小猫", characters=["LinXia"], shots=[]),
            Scene(scene_id="s04", summary="回家洗澡取名豆豆", characters=["LinXia"], shots=[]),
            Scene(scene_id="s05", summary="发现项圈下的车票", characters=["LinXia"], shots=[]),
            Scene(scene_id="s06", summary="寻找老奶奶归还", characters=["LinXia"], shots=[]),
        ],
        next_scene_seq=7,
    )


# ---------- 任务列表（20 个，覆盖各类） ----------
# (label, content, expected_fn(before_proj, after_proj, result) -> bool)
TASKS = [
    # 连续创建 3/5/8
    ("创建3段", "请在剧本末尾连续新增3段：1.小猫和邻居小孩玩耍 2.带小猫看兽医 3.半年后小猫长大了",
     lambda b, a, r: len(a.scenes) >= len(b.scenes) + 3),
    ("创建5段", "请在末尾连续新增5段故事，关于小猫成长的不同阶段",
     lambda b, a, r: len(a.scenes) >= len(b.scenes) + 5),
    ("创建8段", "请在末尾连续新增8段小猫的日常生活故事",
     lambda b, a, r: len(a.scenes) >= len(b.scenes) + 8),
    ("创建2段中间插入", "请在 s03 之后插入2段新故事",
     lambda b, a, r: len(a.scenes) >= len(b.scenes) + 2),
    ("创建4段不同位置", "请在 s01 后插1段、s05 后插1段、末尾加2段，共4段",
     lambda b, a, r: len(a.scenes) >= len(b.scenes) + 4),
    # 批量修改多个段
    ("修改3段summary", "请把 s01、s02、s03 三段的 summary 都改成更详细的描述",
     lambda b, a, r: r.get("scene_updated") and True),
    ("修改2段", "请修改 s04 和 s05 的 summary",
     lambda b, a, r: r.get("scene_updated") and True),
    ("修改4段characters", "请给 s01 s02 s03 s04 都增加一个新角色'奶奶'",
     lambda b, a, r: r.get("scene_updated") and True),
    # 互换
    ("互换s01s02", "请把第1段和第2段互换位置",
     lambda b, a, r: r.get("moved") and True),
    ("互换s03s05", "请交换 s03 和 s05 的位置",
     lambda b, a, r: r.get("moved") and True),
    ("互换s02s04", "请把第2段和第4段互换",
     lambda b, a, r: r.get("moved") and True),
    # 连续移动
    ("s06移到第2位", "请把最后一段 s06 移动到第2位",
     lambda b, a, r: r.get("moved") and True),
    ("s01移到末尾", "请把第1段 s01 移动到最后",
     lambda b, a, r: r.get("moved") and True),
    ("连续移动2段", "请把 s05 移到第3位，再把 s06 移到第1位",
     lambda b, a, r: r.get("moved") and True),
    # 删除 + 创建
    ("删s03建1段", "请删除 s03，并在末尾新增1段新故事",
     lambda b, a, r: r.get("deleted") and r.get("created")),
    ("删s02建2段", "请删掉 s02，然后创建2段新故事",
     lambda b, a, r: r.get("deleted") and r.get("created")),
    ("删s04建3段", "请删除 s04，并连续新增3段",
     lambda b, a, r: r.get("deleted") and r.get("created")),
    # 编号变化后第N条操作
    ("删s01后移第2条到末尾", "请先删除第1段 s01，然后把新的第2段移动到最后",
     lambda b, a, r: r.get("deleted") and r.get("moved")),
    ("删s02后改第3条", "请先删除 s02，然后修改新的第3段的 summary 为'转折点'",
     lambda b, a, r: r.get("deleted") and r.get("scene_updated")),
    ("删s01s02后改第1条", "请先删除 s01 和 s02（两段），然后修改新的第1段的 summary",
     lambda b, a, r: r.get("deleted") and r.get("scene_updated")),
]


def run_task(usage_client, label, content, expected, group):
    """跑单个任务，返回指标 dict。"""
    import src.agents.script_writer as sw
    from src.agents.script_writer import script_writer_turn
    from src.core.store import ProjectStore
    from src.agents.runtime import AgentRuntime

    pd = ROOT / "projects" / f"__sw_ab_{group}_{label}"
    # 清掉旧项目目录保证干净
    import shutil
    if pd.exists():
        shutil.rmtree(pd)
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "output").mkdir(exist_ok=True)
    ProjectStore(pd).save(build_proj())
    before = ProjectStore(pd).load()

    rt = AgentRuntime(project_dir_resolver=lambda name: pd,
                      settings_path=pd / "agents_data" / "settings.json",
                      client=usage_client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]

    collector = Collector()
    usage_client.reset()

    # B 组：monkeypatch _build_system 加软规则；A 组：恢复原函数
    orig_build = sw._build_system
    if group == "B":
        sw._build_system = lambda proj: orig_build(proj) + SOFT_RULE
    else:
        sw._build_system = orig_build

    t0 = time.perf_counter()
    failed = None
    result = None
    try:
        result = asyncio.run(script_writer_turn(rt, "demo", sid, content,
                                                    thinking=False, event_cb=collector))
    except Exception as e:
        failed = str(e)[:200]
    finally:
        sw._build_system = orig_build  # 恢复

    elapsed = time.perf_counter() - t0
    after = ProjectStore(pd).load()
    ok = False
    if failed is None and result is not None:
        try:
            ok = bool(expected(before, after, result))
        except Exception:
            ok = False

    return {
        "label": label, "group": group,
        "write_gen": collector.write_gen,
        "write_exec": collector.write_exec,
        "write_reject": collector.write_reject,
        "llm_calls": usage_client.llm_calls,
        "prompt_tokens": usage_client.prompt,
        "completion_tokens": usage_client.completion,
        "reasoning_tokens": usage_client.reasoning,
        "elapsed_s": round(elapsed, 1),
        "ok": ok,
        "failed": failed,
        "intent": result.get("intent") if result else None,
    }


def main():
    print("=" * 70)
    print("AI-7D-R 多写工具调用 A/B 测试（A=现状, B=加软规则prompt）")
    print("=" * 70)

    real = make_real_client()
    usage_client = UsageTrackingClient(real)

    all_results = []
    for group in ("A", "B"):
        print(f"\n===== 组 {group} =====")
        for label, content, expected in TASKS:
            print(f"[{group}] {label} ...", end=" ", flush=True)
            r = run_task(usage_client, label, content, expected, group)
            all_results.append(r)
            print(f"gen={r['write_gen']} exec={r['write_exec']} rej={r['write_reject']} "
                  f"llm={r['llm_calls']} tok={r['prompt_tokens']}+{r['completion_tokens']}(r{r['reasoning_tokens']}) "
                  f"{r['elapsed_s']}s ok={r['ok']}" + (f" FAIL={r['failed']}" if r['failed'] else ""))

    # 保存原始结果
    out = ROOT / "详细流程文件" / "修复阶段" / "AI-7D-R_SERIAL_AB_RAW.json"
    out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始结果已存：{out}")

    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    for group in ("A", "B"):
        rs = [r for r in all_results if r["group"] == group]
        n = len(rs)
        ok_n = sum(1 for r in rs if r["ok"])
        gen_total = sum(r["write_gen"] for r in rs)
        exec_total = sum(r["write_exec"] for r in rs)
        rej_total = sum(r["write_reject"] for r in rs)
        llm_total = sum(r["llm_calls"] for r in rs)
        pt = sum(r["prompt_tokens"] for r in rs)
        ct = sum(r["completion_tokens"] for r in rs)
        rt_tok = sum(r["reasoning_tokens"] for r in rs)
        elapsed_total = sum(r["elapsed_s"] for r in rs)
        multi_write_tasks = sum(1 for r in rs if r["write_gen"] > 0 and r["write_gen"] > r["write_exec"])  # 有拒绝的任务数
        # 平均每任务浪费的 LLM 轮次/token（因串行拒绝重发）
        print(f"\n组 {group}（{n} 任务，{ok_n} 正确完成）:")
        print(f"  write_generated={gen_total}  executed={exec_total}  serialization_rejected={rej_total}")
        print(f"  发生串行拒绝的任务数={multi_write_tasks}/{n}（{round(100*multi_write_tasks/n)}%）")
        print(f"  llm_calls 总={llm_total}  平均={round(llm_total/n,1)}")
        print(f"  prompt_tokens={pt}  completion_tokens={ct}  reasoning_tokens={rt_tok}")
        print(f"  总耗时={round(elapsed_total,1)}s  平均={round(elapsed_total/n,1)}s")


if __name__ == "__main__":
    main()
