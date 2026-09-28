# -*- coding: utf-8 -*-
# _37_fable/_40_e2e_driver.py
# Phase 39 Phase C——Crane_Lake_P39C 受控 LIVE Canary 驱动。
#
# 纪律（用户 Canary Gate 定版）：
#   - 全程走 Producer 正式 REST 流式入口（/agents/sessions/{sid}/turn/stream，
#     thinking=true——与最终网页产品路径同源），zcode 不替用户批准任何 major；
#   - Canary Gate：先只生成前 2 镜；两镜完成并校准后停下向用户汇报，
#     放行后才继续 3-6 镜；
#   - 出现 evidence pipeline 异常 / 路由误判 / Agent 绕 gate → 立即 STOP；
#   - 每 turn 完整 transcript 存档 _37_fable/_40_e2e/。
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
PROJ = "Crane_Lake_P40A"
OUT_DIR = ROOT / "_37_fable" / "_40_e2e"


def ensure_producer_session() -> str:
    r = httpx.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
                   json={"agent_id": "producer", "scope_type": "project",
                         "scope_id": ""}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("session_id") or d.get("id") or d["session"]["session_id"]


def stream_turn(session_id: str, content: str, timeout_s: float = 3600.0) -> dict:
    """流式 producer turn（thinking=true），返回完整事件流 + 终态 result。"""
    events = []
    result = None
    with httpx.stream(
        "POST",
        f"{BASE}/api/project/{PROJ}/agents/sessions/{session_id}/turn/stream",
        json={"content": content, "thinking": True}, timeout=timeout_s,
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            events.append(ev)
            t = ev.get("type")
            if t == "tool.completed":
                print(f"  [tool] {ev.get('name')}", flush=True)
            elif t == "tool.progress":
                print(f"  [progress] {(ev.get('message') or '')[:90]}", flush=True)
            elif t == "turn.completed":
                result = ev.get("result") or {}
                break
            elif t == "turn.failed":
                raise RuntimeError(f"turn.failed: {ev.get('error')}")
    if result is None:
        raise RuntimeError("流结束但无 turn.completed")
    return {"events": events, "result": result}


def save_transcript(name: str, data: dict) -> pathlib.Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


CANARY_BRIEF = """【Phase 39C 受控 LIVE Canary · 第一步：预编剧冻结（本阶段禁止生成）】

项目：Crane_Lake_P39C（全新独立项目；Visual Profile 已设：2d_animation，优美自然风光——大鹤与森林湖泊，光影柔和，色彩清雅，构图舒展）。

用户故事：一只大鹤在天上飞，下方是一片森林，然后遇到了一个湖，于是大鹤下降落到湖中心。风格不限（按项目 Profile），注意场景要优美。

本轮目标（只做预编剧冻结，绝不生成任何图/视频）：
1) 委派 Script Writer 完成预编剧冻结：把故事规划为 6 个镜头（天→林→湖的完整弧线），每个镜头必须冻结六要素——description（画面内容）、motion（动作）、expected_tail（尾帧预期，校准元数据）、duration（时长，参考配方：静态/反应 5s、动态 8s、高运动/复杂交互 10s）、opening intent（开场承接意图，首句要能自然吸收上一镜 minor 级偏差）、negative constraints（负面约束，如全片仅一只大鹤、禁其他动物/人物、森林与湖的地理要素各自明确）。
2) 故事段结构：按地点/节奏合理分段（如 s01 天空与森林、s02 湖泊），镜头数合计 6。
3) 完成后用 shot/story 结构落库（Script Writer 的正式写工具），并把冻结计划的全文（逐镜六要素）写进最终回复供用户审阅。

【Canary 纪律】本轮严禁任何生成动作（分镜图/视频都不生成）；后续角色/场景/生成由用户逐阶段放行。"""


def cmd_turn(name: str, content: str):
    sid = ensure_producer_session()
    print(f"[producer session] {sid}")
    t0 = time.time()
    data = stream_turn(sid, content)
    data["meta"] = {"turn": name, "latency_s": round(time.time() - t0, 1),
                    "project": PROJ, "thinking": True}
    p = save_transcript(name, data)
    r = data["result"]
    msg = r.get("assistant_message") or ""
    receipts = [x.get("operation") for x in (r.get("receipts") or [])
                if isinstance(x, dict)]
    print(f"\n[done] {name} latency={data['meta']['latency_s']}s ops={receipts}")
    print(f"[transcript] {p}")
    print(f"\n[assistant_message]\n{msg[:2500]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--content-file", required=True)
    a = ap.parse_args()
    cmd_turn(a.name, pathlib.Path(a.content_file).read_text(encoding="utf-8"))
