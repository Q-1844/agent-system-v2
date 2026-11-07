# tests/test_daily_video_seconds_knob.py
# 🔴 2026-11-08 额度墙压缩模拟旋钮的结构锁（用户拍板：模拟按天额度耗尽→
# 停 20min→定时器唤醒→接续，替代每天只能一次的真实跨天实测）。
# 纪律：默认必须恒为 500（真实供应商口径，2026-11-03 TA 核实）；旋钮只在
# workflow_dispatch 时显式传小值，用于让本地账本预检提前触墙
# （QuotaLedger.can 失败 → AgnesClient raise DailyQuotaExhausted →
# pause_for_quota，闸门=北京次日 00:10）。红了先怀疑自己改了不该改的。
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github/workflows/video-autoproduction-e2e.yml"

_PROBE = ("import tools.full_project_cloud_acceptance as m; "
          "print(m.DAILY_VIDEO_SECONDS)")


def _probe(extra_env):
    env = {k: v for k, v in os.environ.items() if k != "DAILY_VIDEO_SECONDS"}
    env.update(extra_env)
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=REPO, env=env,
                         capture_output=True, text=True, timeout=180, check=True)
    return out.stdout.strip()


def test_default_stays_500_without_env():
    """真实口径：不传 env 时恒为 500（供应商唯一硬约束，勿动）。"""
    assert _probe({}) == "500"


def test_env_override_trips_early_wall():
    """旋钮生效：小值限制进入账本 limits，本地预检提前触墙。"""
    assert _probe({"DAILY_VIDEO_SECONDS": "96"}) == "96"


def test_env_empty_means_unset():
    """空串=未设置（GitHub Actions env 语义）→ 500；垃圾值则 int() 直接
    ValueError fail-loud（宁可炸启动也不静默跑错模拟档）。"""
    assert _probe({"DAILY_VIDEO_SECONDS": ""}) == "500"


def test_workflow_wires_the_knob():
    """接线锁：workflow 必须有输入定义 + env 传递 + 默认 500。"""
    yml = WF.read_text(encoding="utf-8")
    assert "daily_video_seconds:" in yml
    assert "DAILY_VIDEO_SECONDS: ${{ inputs.daily_video_seconds }}" in yml
    tail = yml.split("daily_video_seconds:", 1)[1][:400]
    assert "default: 500" in tail  # 默认值必须钉在输入定义处
