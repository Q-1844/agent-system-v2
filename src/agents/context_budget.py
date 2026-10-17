# src/agents/context_budget.py
# Phase55 D（任务书 §7）：会话上下文预算监控与用量账本。
#
# 背景（P54-B1 实证，backend_p54.log:788）：单 producer 会话累积 467,222 input
# tokens + 65,536 completion 预留 > provider 524,288 硬上限 → 400 中断 → 人工
# session-split。本模块把「下一请求预算」变成被监控的一等数据：
#   - 真实 usage（provider 返回，Phase55 D 起非流式/流式均透传）按会话落账；
#   - 下一请求估算 = 字符估算（保守系数）+ 媒体 token + 输出预留，与
#     provider 上限（config，不硬编码报告数字）对比；
#   - 接近阈值 → 由 tool-loop 停止发起新推理，转 handoff（src/agents/handoff.py）。
# 累计消费 tokens 记成本（stats/额度口径），绝不冒充「当前上下文长度」。
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 默认值：仅当 config 未配置时兜底（任务书 §7：阈值按真实模型/Provider 配置，
# 不能硬编码报告的 467k/524k——默认值同为可配置项而非结论）。
_DEFAULTS = {
    "limit_tokens": 524288,        # provider 上下文硬上限（input+completion）
    "output_reserve_tokens": 65536,  # producer 恒定 max_tokens 输出预留
    "handoff_ratio": 0.85,         # est_input + reserve ≥ limit*ratio → 交接
    "chars_per_token": 2.0,        # 保守估算（中英混合实测区间约 1.6-4；取偏小值=高估 token）
    "media_budget_tokens": 0,      # 正文 tool-loop 不含图（base64 不进 messages）；预留给多模态扩展
}


def _cfg() -> dict:
    try:
        from deps import _APP_CFG
        c = (_APP_CFG or {}).get("context") or {}
    except Exception:
        c = {}
    out = dict(_DEFAULTS)
    for k, v in c.items():
        if k in out and v is not None:
            out[k] = v
    return out


def estimate_request_tokens(*, system_chars: int, tools_chars: int,
                            history_chars: int, media_tokens: int = 0,
                            cfg: Optional[dict] = None) -> dict:
    """下一请求输入 token 估算 + 预算判定。
    返回 {est_input_tokens, output_reserve, limit, handoff, usage_note}。
    est = ceil((system+tools+history)/chars_per_token) + media_tokens。
    handoff 判定：est + output_reserve ≥ limit * handoff_ratio（接近上限即交接，
    而非撞墙——安全余量在 ratio 里）。"""
    c = cfg or _cfg()
    total_chars = max(0, int(system_chars)) + max(0, int(tools_chars)) \
        + max(0, int(history_chars))
    est = -(-total_chars // float(c["chars_per_token"]))  # ceil
    est = int(est) + int(media_tokens or c["media_budget_tokens"])
    reserve = int(c["output_reserve_tokens"])
    limit = int(c["limit_tokens"])
    handoff = (est + reserve) >= limit * float(c["handoff_ratio"])
    return {"est_input_tokens": est, "output_reserve": reserve, "limit": limit,
            "handoff": handoff,
            "usage_note": "字符保守估算（非精确 tokenize）——真实 usage 见 UsageLedger"}


class UsageLedger:
    """per-session 真实 usage 账本（append-only JSONL，一行一次 LLM 调用）。
    用途：①token 预算监控的真实锚点（last prompt_tokens）；②§12 统计口径
    （text tokens/调用数按会话归集）。文件缺失/损坏=诚实零账本（不估算冒充）。"""

    def __init__(self, pdir: Path, session_id: str):
        self.path = Path(pdir) / "output" / "agents" / "usage" \
            / f"{session_id}.jsonl"

    def record(self, *, kind: str, usage: Optional[dict], step: int = -1) -> dict:
        rec = {"ts": time.time(), "kind": kind, "step": step,
               "prompt_tokens": (usage or {}).get("prompt_tokens"),
               "completion_tokens": (usage or {}).get("completion_tokens"),
               "total_tokens": (usage or {}).get("total_tokens"),
               "image_tokens": ((usage or {}).get("prompt_tokens_details") or {})
               .get("image_tokens")}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass   # 账本失败不阻断生产（诚实缺失：估算与真实 usage 均注明来源）
        return rec

    def last_prompt_tokens(self) -> Optional[int]:
        """最近一次观察到的 input tokens（下一请求规模的最好真实锚点）。"""
        try:
            if not self.path.exists():
                return None
            last = None
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("prompt_tokens"):
                        last = int(r["prompt_tokens"])
            return last
        except Exception:
            return None

    def totals(self) -> dict:
        """累计消费（成本口径——不是上下文长度）。"""
        calls = 0
        prompt = completion = 0
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        calls += 1
                        prompt += int(r.get("prompt_tokens") or 0)
                        completion += int(r.get("completion_tokens") or 0)
        except Exception:
            pass
        return {"calls": calls, "prompt_tokens": prompt,
                "completion_tokens": completion}

    def handoff_anchor(self) -> Optional[dict]:
        """交接锚点：最近真实 input 规模（交接包用——新会话据此判断紧迫度）。"""
        lp = self.last_prompt_tokens()
        return None if lp is None else {"last_observed_prompt_tokens": lp,
                                        "observed_at": time.time()}
