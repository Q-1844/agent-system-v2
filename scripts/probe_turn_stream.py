"""AI-7D-R · /turn/stream 端到端探测脚本（throwaway 项目，定位事件时序）。

建一个临时项目 → 创建 script_writer 会话 → 发 /turn/stream（thinking=False）→
打印每个 SSE 事件的 type/step/name/ok/delta，定位 assistant.delta 与 tool 事件的
真实顺序（验证是否"执行工具时结果就输出了"）。
"""
import json
import sys
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8000"
P = "__sw_multiwrite"  # throwaway


def _ensure_project():
    """直接用 ProjectStore 建 throwaway 项目（绕过 API）。"""
    from src.core.store import ProjectStore
    from src.core.models import Project, Scene, Character, StyleAnchor
    pdir = ROOT / "projects" / P
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "output").mkdir(exist_ok=True)
    proj = Project(
        name=P, style=StyleAnchor(),
        story_text="小雨撑着伞走在雨夜的街道上。她发现了一只淋湿的小猫，用外套把它裹起来带回了家。回家后给小猫洗了澡，取名豆豆。第二天她在小猫项圈下发现了一张车票，于是开始寻找它的主人老奶奶。",
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
    ProjectStore(pdir).save(proj)
    print(f"[setup] 项目已建：{pdir}/output/project.json（6 段）")


def main():
    _ensure_project()

    # 1. 创建 script_writer 会话
    r = httpx.post(f"{BASE}/api/project/{P}/agents/sessions",
                   json={"agent_id": "script_writer", "scope_type": "project", "scope_id": P},
                   timeout=15)
    r.raise_for_status()
    sid = r.json()["session_id"]
    print(f"[session] {sid}")

    # 2. /turn/stream（多写任务，thinking=False）—— 测模型是否一次发多个写工具 + 串行拒绝
    content = "请在剧本末尾连续新增 3 段故事：第1段小猫和邻居小孩玩耍，第2段带小猫看兽医，第3段半年后小猫长大了"
    print(f"[send] content={content!r} thinking=False")
    print("=" * 60)

    n = 0
    assistant_text = ""
    with httpx.stream("POST", f"{BASE}/api/project/{P}/agents/sessions/{sid}/turn/stream",
                      json={"content": content, "thinking": False}, timeout=120) as resp:
        print(f"[resp] HTTP {resp.status_code}  content-type={resp.headers.get('content-type')}")
        if resp.status_code != 200:
            print(resp.read().decode("utf-8", "replace")[:500])
            return
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                print(f"[{n:3d}] [DONE]")
                break
            try:
                ev = json.loads(payload)
            except Exception:
                print(f"[{n:3d}] UNPARSABLE: {payload[:80]}")
                continue
            t = ev.get("type")
            extra = ""
            if t == "assistant.delta":
                assistant_text += ev.get("delta", "")
                extra = f" delta={ev.get('delta','')!r}"
            elif t.startswith("tool"):
                extra = f" step={ev.get('step_id')} name={ev.get('name')} ok={ev.get('ok')} summary={ev.get('summary','')!r}"
            elif t.startswith("reasoning"):
                extra = f" step={ev.get('step_id')} delta_len={len(ev.get('delta',''))}"
            elif t == "turn.completed":
                extra = f" result.intent={ev.get('result',{}).get('intent')}"
            print(f"[{n:3d}] {t}{extra}")
            n += 1

    print("=" * 60)
    print(f"[assistant 累积] {assistant_text!r}")
    print(f"[事件总数] {n}")


if __name__ == "__main__":
    main()
