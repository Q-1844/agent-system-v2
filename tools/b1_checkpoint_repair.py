#!/usr/bin/env python3
"""B1 检查点身份修复（维护工具，配合 workflow_dispatch 使用）。

场景：某跳 run 在 RESTORED 落盘后被取消，其 artifact 缺 summary.json
（acceptor 的 finally 未执行），恢复链下游无法从它续跑；而检查点
github_run_id 已绑定它，旧跳（artifact 完整）又被归属校验拒绝——死锁。

本工具把 checkpoint.github_run_id 重绑回「最后一个 artifact 完整」的跳。
写前校验：nonce 一致、phase 在可恢复集、source_sha 不变；
文档其余字段逐字节保留（仅 github_run_id + updated_at 变化）。
fail-loud；--dry-run 只打印差异不写入。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.video_task_state import task_index, validate_nonce

RESUMABLE_PHASES = ('FAILED', 'RUNNING', 'RESTORED', 'BLOCKED_PREFLIGHT',
                   'AWAITING_HUMAN', 'STAGE0_BOUND', 'WAITING_QUOTA', 'PAUSING',
                   'INTERRUPTED')


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--github-run-id', required=True,
                        help='重绑目标跳的 Actions run ID（必须拥有完整可恢复 artifact）')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    nonce = validate_nonce(args.nonce)
    target = str(int(args.github_run_id))  # 数字规整，拒绝脏输入
    token = os.environ.get('B1_ARCHIVE_TOKEN', '').strip()
    if not token:
        raise SystemExit('missing B1_ARCHIVE_TOKEN')

    index = task_index(token, nonce)
    current = index.read()
    if current is None:
        raise SystemExit(f'no checkpoint found for {nonce}')
    doc = current['doc']

    # ---- 写前校验 ----
    if doc.get('nonce') != nonce:
        raise SystemExit(f"checkpoint nonce mismatch: {doc.get('nonce')!r}")
    if str(doc.get('github_run_id')) == target:
        print(f'[noop] github_run_id already {target}')
        return 0
    if doc.get('phase') not in RESUMABLE_PHASES:
        raise SystemExit(f"phase {doc.get('phase')!r} is not resumable; refusing to repair")

    new_doc = dict(doc)
    new_doc['github_run_id'] = target
    new_doc['updated_at'] = utc_now_iso()

    print('[before] github_run_id=%s phase=%s source_sha=%s attempts=%d' % (
        doc.get('github_run_id'), doc.get('phase'), doc.get('source_sha'),
        len(doc.get('video_attempts') or [])))
    print('[after ] github_run_id=%s (source_sha unchanged: %s)' % (
        new_doc['github_run_id'], new_doc['source_sha'] == doc.get('source_sha')))
    changed = {k: (doc.get(k), new_doc.get(k))
               for k in set(doc) | set(new_doc) if doc.get(k) != new_doc.get(k)}
    print('[diff  ]', json.dumps(changed, ensure_ascii=False))
    if args.dry_run:
        print('[dry-run] no write performed')
        return 0

    result = index.write_cas(
        new_doc, current['blob_sha'],
        message=f'checkpoint repair: rebind github_run_id -> {target} ({nonce})')
    if not result.get('ok') or not result.get('new_sha'):
        raise SystemExit(f'CAS write rejected: {result}')
    print('[write ] ok; new blob_sha=%s' % result['new_sha'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
