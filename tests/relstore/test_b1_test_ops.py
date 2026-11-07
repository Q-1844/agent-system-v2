# tests/relstore/test_b1_test_ops.py
# 🔴 2026-11-08 额度墙压缩模拟工具的结构与守卫锁。
# 背景（用户拍板）：真实跨天接续每天只能测一次；压缩模拟走真实链路
# （额度暂停→wakeup 20min 定时器→闸门开→派发接续），本工具只做
# 「闸门压缩」与「卡死检查点关闭」两个安全操作。
# 红了先怀疑自己改了不该改的（HANDOFF_starter_zombie_and_quota_sim_20261108.md）。
from pathlib import Path

from tools.b1_test_ops import can_close, can_compress_gate

REPO = Path(__file__).resolve().parents[2]


# ---------- 守卫：闸门压缩只认额度暂停形态 ----------

def test_gate_compress_requires_waiting_quota_phase():
    ok, _ = can_compress_gate({'phase': 'WAITING_QUOTA',
                               'terminal_status': 'quota_exhausted'})
    assert ok


def test_gate_compress_refuses_running_and_complete():
    for doc in ({'phase': 'RUNNING', 'terminal_status': None},
                {'phase': 'COMPLETE', 'terminal_status': None},
                {'phase': 'WAITING_QUOTA', 'terminal_status': 'paused'}):
        ok, reason = can_compress_gate(doc)
        assert not ok, doc


# ---------- 守卫：关闭只认卡死形态，终态/人工相位拒绝 ----------

def test_close_allows_stuck_running():
    ok, _ = can_close({'phase': 'RUNNING', 'terminal_status': None})
    assert ok


def test_close_is_idempotent_noop_when_already_failed():
    ok, reason = can_close({'phase': 'FAILED', 'terminal_status': 'failed'})
    assert not ok and reason == 'noop'


def test_close_refuses_terminal_and_human_phases():
    for phase in ('COMPLETE', 'AWAITING_HUMAN', 'BLOCKED_PREFLIGHT', 'STARTING'):
        ok, reason = can_close({'phase': phase, 'terminal_status': None})
        assert not ok and 'untouchable' in reason, phase


# ---------- 接线锁：工具与 workflow 语义 ----------

def test_tool_is_cas_single_file_no_dispatch_semantics():
    src = (REPO / 'tools/b1_test_ops.py').read_text(encoding='utf-8')
    assert 'index.write_cas' in src          # 单文件 CAS（同 b1_checkpoint_repair 纪律）
    assert "validate_nonce" in src           # 脏 nonce 拒绝
    assert '互斥' in src                     # 两个操作一次只做一个
    assert 'quota_resume_after' in src       # 闸门字段名与 quota_gate 一致
    # 不派发任何 run（反递归纪律）
    assert 'workflow' not in src.split('"""', 2)[2].replace('workflow_dispatch', '')


def test_workflow_wires_both_ops_with_token_guard():
    yml = (REPO / '.github/workflows/b1-test-ops.yml').read_text(encoding='utf-8')
    assert 'nonce' in yml and 'gate_minutes' in yml and 'close_reason' in yml
    assert 'dry_run' in yml
    assert 'B1_ARCHIVE_TOKEN: ${{ secrets.B1_ARCHIVE_TOKEN }}' in yml
    assert 'python tools/b1_test_ops.py' in yml
    # 闸门压缩必须显著标注仅测试用（防误用于真实生产）
    assert '仅压缩模拟测试用' in yml or '真实生产派发禁用' in yml
