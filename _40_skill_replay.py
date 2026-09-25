# -*- coding: utf-8 -*-
"""40.md Phase D — Skill discovery/loading/workflow replay（真实 API，Agent 正式入口）。

设计：
- 真实 AgnesClient（config/config.yaml + AGNES_API_KEY env）+ 进程内正式入口
  director_turn（HTTP 端点只是它的薄封装；workflow decision 全部来自 Agent +
  Skill Runtime，无编排脚本代办）。
- 任务为校准类只读任务（不生成视频，控制成本）：观察 Agent 是否（在 hint/catalog
  帮助下）自主 skill.load 正确技能、并按技能方法描述工作流。
- 对照指标（40.md §十六）：correct skill activation / false activation /
  tool selection / trace completeness / 回复中技能方法锚点命中。
- --baseline 模式：monkeypatch 关闭 hint 注入（Agent 只能凭 L1 catalog 自主发现），
  与正常模式对比 discovery 可靠性。

用法：
  python _40_skill_replay.py             # 正常模式（hint + catalog）
  python _40_skill_replay.py --baseline  # 关闭 hint（仅 catalog）
  python _40_skill_replay.py --task "自定义任务文本"
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

PROJECT = "_40_skill_replay"
BASE_DIR = Path(__file__).parent


def _make_client():
    import yaml
    # .env 手动加载（AGNES_API_KEY 等；脚本不经 api_server startup）
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    cfg_path = BASE_DIR / "config" / "config.yaml"
    config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if "${AGNES_API_KEY}" in str(config["agnes"].get("api_key", "")):
        config["agnes"]["api_key"] = os.environ.get("AGNES_API_KEY", "")
    assert config["agnes"].get("api_key"), "AGNES_API_KEY 未配置"
    from src.api.agnes_client import AgnesClient
    return AgnesClient(config["agnes"])


async def _run(task: str, baseline: bool) -> dict:
    from src.core.store import ProjectStore
    from src.core.models import Project, Scene, Shot
    from src.agents.runtime import AgentRuntime

    pdir = BASE_DIR / "projects" / PROJECT
    pdir.mkdir(parents=True, exist_ok=True)
    if not (pdir / "project.json").is_file():
        # 两镜接力结构（校准话题有真实对象可读，Agent 不至于只能空谈）
        shots = [
            Shot(shot_id="s01_c01", scene_id="s01", index=1,
                 description="小铃在村口土路上奔跑", characters=["小铃"],
                 target_duration_s=5),
            Shot(shot_id="s01_c02", scene_id="s01", index=2,
                 description="小铃在谷仓门口停下，喘气", characters=["小铃"],
                 target_duration_s=5, source_shot_id="s01_c01",
                 link_mode="multi"),
        ]
        ProjectStore(pdir).save(Project(
            name=PROJECT, story_text="小铃跑过村口，在谷仓门口停下。",
            scenes=[Scene(scene_id="s01", summary="小铃跑过村口到谷仓",
                          characters=["小铃"], shots=shots)],
            characters=[]))

    client = _make_client()
    rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                      settings_path=pdir / "agents_data" / "settings.json",
                      client=client)

    if baseline:
        # 对照组：关闭确定性 hint（Agent 只能凭 L1 catalog 自主发现技能）
        from src.agents.skill_system.runtime import get_skill_runtime
        get_skill_runtime().activation_hint = lambda *a, **k: None

    from src.agents.director import director_turn
    store = rt._session_store(PROJECT)
    session = store.get_or_create_conversation("director")

    t0 = time.time()
    result = await director_turn(rt, PROJECT, session.session_id, task)
    dur = time.time() - t0

    meta = (result.get("message") or {}).get("meta") or {}
    skills = meta.get("skills") or []
    trace = result.get("trace") or []
    tool_names = [t.get("tool") for t in trace]
    skill_loads = [t for t in trace if t.get("tool") == "skill.load"]
    loaded_ids = [e.get("skill_id") for e in skills if e["event"] == "load"]
    reply = result.get("assistant_message") or ""

    anchors = {
        "接缝测量/seam": any(k in reply for k in ("接缝测量", "seam_measure", "接缝")),
        "三档裁定": any(k in reply for k in ("三档", "none/minor/major", "裁定")),
        "尾帧接力": "尾帧" in reply or "接力" in reply,
        "trace 落档": "trace" in reply.lower(),
        "预编剧": "预编剧" in reply,
    }
    out = {
        "mode": "baseline(no-hint)" if baseline else "skill(hint+catalog)",
        "task": task, "duration_s": round(dur, 1),
        "correct_skill_activation": bool(skill_loads)
        and "full-story-relay-calibration" in loaded_ids,
        "false_activation": any(
            sid not in ("full-story-relay-calibration", "keyframes-bridge-repair")
            for sid in loaded_ids),
        "loaded_skills": loaded_ids,
        "tool_calls": tool_names,
        "skill_events": [(e["event"], e.get("skill_id")) for e in skills],
        "reply_method_anchors": anchors,
        "reply_head": reply[:500],
        "trace_jsonl": str(pdir / "output" / "agents" / "skills" / "trace.jsonl"),
        "hint_in_persisted_session": any(
            "[系统 skill 提示]" in (m.content or "")
            for m in store.load(session.session_id).messages
            if m.role == "user"),
    }
    return out


def main():
    baseline = "--baseline" in sys.argv
    task = "对整条故事做连续性校准"
    for a in sys.argv[1:]:
        if a.startswith("--task="):
            task = a.split("=", 1)[1]
    print(f"=== mode: {'baseline (no hint)' if baseline else 'skill (hint+catalog)'} ===")
    print(f"task: {task}\n")
    out = asyncio.run(_run(task, baseline))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    # 汇总行（供报告引用）
    print("\n=== SUMMARY ===")
    print(f"correct_skill_activation: {out['correct_skill_activation']}")
    print(f"false_activation: {out['false_activation']}")
    print(f"tool_calls: {out['tool_calls']}")
    print(f"skill_events: {out['skill_events']}")
    print(f"reply anchors: {out['reply_method_anchors']}")
    print(f"hint leaked into session: {out['hint_in_persisted_session']}")


if __name__ == "__main__":
    main()
