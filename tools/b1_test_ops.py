#!/usr/bin/env python3
"""B1 检查点测试/维护操作（配合 workflow_dispatch 使用，2026-11-08 用户拍板）。

两个显式操作，其余字段逐字节保留（仅变更字段 + updated_at）：

  --gate-minutes N   把额度闸门 quota_resume_after 压到 now+N 分钟。
                     仅供「按天额度墙压缩模拟」测试：生产闸门=北京次日
                     00:10（quota_gate.next_quota_reset_utc），真实生产
                     派发不得用它缩短等待。写前校验：必须处于
                     WAITING_QUOTA + quota_exhausted（额度暂停形态）。

  --close-reason S   把卡死检查点置 FAILED/failed 终止。适用：被操作者
                     取消于规划期（快照无 summary.json，死链恢复必败）、
                     需要阻止巡检/wakeup 无限重派的 run。写前校验：
                     phase 不得是 COMPLETE / AWAITING_HUMAN /
                     BLOCKED_PREFLIGHT / STARTING（这些有自身语义）。

反递归 checklist：不派发任何后续 run；写操作仅 B1_ARCHIVE_TOKEN
单文件 CAS；fail-loud；--dry-run 只打印差异不写入。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.video_task_state import task_index, validate_nonce

# 与 resume_dispatch.DEAD_PHASES 同源（不复用 import 以免测试工具耦合生产模块）
UNTOUCHABLE_PHASES = ('COMPLETE', 'AWAITING_HUMAN', 'BLOCKED_PREFLIGHT', 'STARTING')
CLOSEABLE_PHASES = ('RUNNING', 'RESTORED', 'PAUSING', 'INTERRUPTED', 'WAITING_QUOTA',
                    'STAGE0_BOUND', 'FAILED')
GATE_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime(GATE_FORMAT)


def can_compress_gate(doc) -> tuple[bool, str]:
    """仅额度暂停形态可压缩闸门（其他相位拒绝，防误伤生产状态）。"""
    if doc.get('phase') != 'WAITING_QUOTA':
        return False, f"phase {doc.get('phase')!r} is not WAITING_QUOTA"
    if doc.get('terminal_status') != 'quota_exhausted':
        return False, f"terminal {doc.get('terminal_status')!r} is not quota_exhausted"
    return True, 'ok'


def can_close(doc) -> tuple[bool, str]:
    """卡死检查点可关闭；终态/人工相位拒绝（保护既有语义）。"""
    if doc.get('phase') in UNTOUCHABLE_PHASES:
        return False, f"phase {doc.get('phase')!r} is untouchable"
    if doc.get('terminal_status') == 'failed':
        return False, 'noop'  # 已关闭（幂等）
    if doc.get('phase') not in CLOSEABLE_PHASES:
        return False, f"phase {doc.get('phase')!r} is not closeable"
    return True, 'ok'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--gate-minutes', type=int, default=0,
                        help='压缩额度闸门到 now+N 分钟（0=不操作）')
    parser.add_argument('--close-reason', default='',
                        help='关闭检查点的原因（空=不操作）')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if args.gate_minutes and args.close_reason:
        raise SystemExit('gate-minutes 与 close-reason 互斥，一次一个操作')
    if not args.gate_minutes and not args.close_reason:
        raise SystemExit('nothing to do: 传 --gate-minutes N 或 --close-reason "..."')
    if args.gate_minutes < 0:
        raise SystemExit('gate-minutes 必须为正数')

    nonce = validate_nonce(args.nonce)
    token = os.environ.get('B1_ARCHIVE_TOKEN', '').strip()
    if not token:
        raise SystemExit('missing B1_ARCHIVE_TOKEN')

    index = task_index(token, nonce, 'full-project-production.json')
    current = index.read()
    if current is None:
        raise SystemExit(f'no checkpoint found for {nonce}')
    doc = current['doc']
    if doc.get('nonce') != nonce:
        raise SystemExit(f"checkpoint nonce mismatch: {doc.get('nonce')!r}")

    new_doc = dict(doc)
    if args.gate_minutes:
        ok, reason = can_compress_gate(doc)
        if not ok:
            raise SystemExit(f'refuse gate compress: {reason}')
        from tools.quota_gate import parse_gate
        if parse_gate(doc.get('quota_resume_after')) is None:
            raise SystemExit('refuse gate compress: original gate is invalid')
        gate = (dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(minutes=args.gate_minutes)).strftime(GATE_FORMAT)
        new_doc['quota_resume_after'] = gate
        prior = doc.get('quota_test_gate_override') or {}
        original = (prior.get('old_gate') if prior.get('new_gate') == doc.get('quota_resume_after')
                    and str(prior.get('source_run_id')) == str(doc.get('github_run_id'))
                    else doc.get('quota_resume_after'))
        new_doc['quota_test_gate_override'] = dict(
            nonce=nonce, source_run_id=str(doc.get('github_run_id')),
            old_gate=original, new_gate=gate)
        op = f'gate compress -> {gate} (+{args.gate_minutes}min)'
    else:
        ok, reason = can_close(doc)
        if reason == 'noop':
            print(f'[noop] checkpoint already closed: {nonce}')
            return 0
        if not ok:
            raise SystemExit(f'refuse close: {reason}')
        new_doc['phase'] = 'FAILED'
        new_doc['terminal_status'] = 'failed'
        new_doc['error'] = args.close_reason
        op = 'close checkpoint (FAILED/failed)'

    new_doc['updated_at'] = utc_now_iso()
    changed = {k: (doc.get(k), new_doc.get(k))
               for k in set(doc) | set(new_doc) if doc.get(k) != new_doc.get(k)}
    print(f'[before] nonce={nonce} phase={doc.get("phase")} '
          f'terminal={doc.get("terminal_status")} gate={doc.get("quota_resume_after")}')
    print(f'[op    ] {op}')
    print('[diff  ]', json.dumps(changed, ensure_ascii=False))
    if args.dry_run:
        print('[dry-run] no write performed')
        return 0

    result = index.write_cas(
        new_doc, current['blob_sha'],
        message=f'b1 test ops: {op} ({nonce})')
    if not result.get('ok') or not result.get('new_sha'):
        raise SystemExit(f'CAS write rejected: {result}')
    print('[write ] ok; new blob_sha=%s' % result['new_sha'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
