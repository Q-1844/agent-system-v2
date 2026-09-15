# _b1_dialogue_probe/probe_common.py
# Native AV B1 E2E（17.md §15-§18）— 公共库（scratch，不进生产）。
# 模式对齐 _audio_probe/probe_common.py（A0 先例）：
#   - 复用生产 AgnesClient，独立 QuotaLedger（不与生产 cache/quota.json 混写）
#   - RateLimiter video 桶 1 RPM；轮询走 text 桶
# 生产代码 diff = 0：只 import，不改任何生产模块。
from __future__ import annotations
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # animation-storyboard/
PROBE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from src.api.agnes_client import AgnesClient  # noqa: E402
from src.core.quota import QuotaLedger  # noqa: E402
from src.core.rate_limiter import RateLimiter  # noqa: E402

EVID = PROBE / "evidence"
RESULTS = PROBE / "results"
CACHE = PROBE / "cache"
for _d in (EVID, RESULTS, CACHE):
    _d.mkdir(parents=True, exist_ok=True)

FFPROBE = "ffprobe"
PROJECT_NAME = "__native_av_b1_e2e"
LANGUAGE = "Mandarin Chinese"   # §10：显式 caller 输入（创作语料既成事实，来源记录于报告）


# ---------- 配置与客户端 ----------
def load_config():
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    agnes = dict(cfg["agnes"])
    key = agnes.get("api_key") or ""
    if "${" in key:
        m = re.match(r"^\$\{(\w+)\}", key.strip())
        env_name = m.group(1) if m else "AGNES_API_KEY"
        key = ""
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(env_name + "="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    agnes["api_key"] = key
    return cfg, agnes


def make_client() -> tuple[AgnesClient, QuotaLedger]:
    """独立探针客户端。账本上限：video 400s（12 条 ≈78s + 重试余量）。"""
    _cfg, agnes = load_config()
    ledger = QuotaLedger(
        CACHE / "quota.json",
        limits={"video_seconds": 400, "images": 0, "text_calls": 1200})
    client = AgnesClient(
        {"base_url": agnes["base_url"], "api_key": agnes["api_key"],
         "models": agnes["models"], "rpm": agnes.get("rpm"),
         "proxy": agnes.get("proxy") or ""},
        quota=ledger)
    return client, ledger


# ---------- 小工具 ----------
def jwrite(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def jread(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def ev_dir(spec_id: str) -> Path:
    d = EVID / spec_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state() -> dict:
    return jread(PROBE / "state.json", {}) or {}


def save_state(st: dict):
    jwrite(PROBE / "state.json", st)


def ffprobe_media(path: Path) -> dict:
    p = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout or "{}")
    except Exception:
        return {"parse_error": p.stdout[:500], "stderr": p.stderr[:500]}
