# Phase51-E E2：效率指标计算（数据源：events.jsonl + p50_timing_ledger.jsonl）
# 指标（任务书口径）：
#   ① 就绪→提交延迟：n_dir_video attempt 派发(ts) → 每镜 video_submitted(ts)；
#      独立镜 p95≤30s 为达标（接力镜单列——等待上游属设计语义）
#   ② 委派间隙空闲：观察墙钟 − 在途覆盖并集（union coverage，同 C-T03 算法）
#   ③ 吞吐：n_dir_video 派发→全部结算墙钟；对照 C-T03 att2/att4
#   ④ 轮询量与 429：api_server 日志 poll/429 计数
#   ⑤ 批量委派行为：video_submitted 时间戳分布（聚集提交=纪律生效）
import json, sys, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from datetime import datetime, timezone

RUN = 'run_58a5f25aeeec'
EV = Path(f'projects/P50_B/output/workflows/runs/{RUN}/events.jsonl')
LED = Path('cache/p50_timing_ledger.jsonl')


def ts_parse(s, fake_utc=False):
    """events 的 ts 值=本地钟但带 Z 后缀（伪 UTC）→ fake_utc=True 时减 8h 修正为真 epoch；
    ledger 的 utc=真 UTC 正常解析。"""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace('Z', '+00:00')
    t = datetime.fromisoformat(s).timestamp()
    return t - 8 * 3600 if fake_utc else t


def main():
    evts = [json.loads(l) for l in EV.read_text(encoding='utf-8').splitlines() if l.strip()]
    dispatch_ts = None
    for e in evts:
        if e.get('kind') == 'node.state' and e.get('nodeId') == 'n_dir_video' \
                and e.get('to') == 'dispatching':
            if dispatch_ts is None:
                dispatch_ts = ts_parse(e.get('ts'), fake_utc=True)
    print(f"n_dir_video dispatch: {datetime.fromtimestamp(dispatch_ts).strftime('%H:%M:%S') if dispatch_ts else '尚未派发'}")

    # 账本：本 run 窗口内的 P50_B 视频事件
    rows = []
    for l in LED.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get('project') != 'P50_B' or r.get('event') not in (
                'video_submitted', 'video_downloaded', 'video_failed'):
            continue
        rows.append(r)

    if dispatch_ts:
        win = [r for r in rows if ts_parse(r.get('utc', r.get('epoch', 0))) >= dispatch_ts - 1]
        subs = {}
        for r in win:
            if r['event'] == 'video_submitted':
                subs.setdefault(r.get('shot_id'), []).append(ts_parse(r['utc']))
        dl = {}
        for r in win:
            if r['event'] == 'video_downloaded':
                dl.setdefault(r.get('shot_id'), []).append(ts_parse(r['utc']))
        print(f"\n窗口内提交事件 {len(win)} 条；镜头 {len(subs)} 个")
        print("\n--- ① 每镜就绪→提交延迟（相对 dispatch）---")
        lats = []
        for sid, tss in sorted(subs.items(), key=lambda kv: kv[1][0]):
            lat = tss[0] - dispatch_ts
            lats.append((sid, lat))
            print(f"  {sid}: +{lat:6.1f}s  (submissions={len(tss)})")
        indep = [x for x in lats if x[0] not in ()]  # 接力镜名单运行时补
        if indep:
            vals = sorted(x[1] for x in indep)
            p50 = vals[len(vals)//2]
            p95 = vals[min(len(vals)-1, int(len(vals)*0.95))]
            print(f"  提交延迟 p50={p50:.1f}s p95={p95:.1f}s（含接力镜；接力镜单列见上）")
        print("\n--- ⑤ 批量委派（前 30s 内提交数）---")
        n30 = sum(1 for _, lat in lats if lat <= 30)
        print(f"  dispatch 后 30s 内提交 {n30}/{len(lats)}")
        print("\n--- ③ 吞吐 ---")
        if dl:
            last_dl = max(max(v) for v in dl.values())
            total = last_dl - dispatch_ts
            print(f"  dispatch→最后收货: {total/60:.1f} min（{len(dl)} 镜已收货）")
        print("\n--- ② 委派间隙（union coverage）---")
        ivs = []
        for r in win:
            if r['event'] == 'video_submitted':
                ivs.append([ts_parse(r['utc']), None])
            elif r['event'] in ('video_downloaded', 'video_failed'):
                # 配对最近一个未闭合的提交（同 shot 优先）
                sid = r.get('shot_id')
                for iv in reversed(ivs):
                    if iv[1] is None:
                        iv[1] = ts_parse(r['utc'])
                        break
        cov = sorted([iv for iv in ivs if iv[1]])
        merged = []
        for s, e in cov:
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        covered = sum(e - s for s, e in merged)
        if dl:
            last_dl = max(max(v) for v in dl.values())
            total = last_dl - dispatch_ts
            idle = total - covered
            print(f"  墙钟 {total/60:.1f}min − 在途并集 {covered/60:.1f}min = 空闲 {idle/60:.1f}min"
                  f"（占 {100*idle/total if total else 0:.0f}%；对照 C-T03 att2 空档）")
    print("\n--- ④ 轮询与 429（日志）---")
    log = Path('logs/api_server_p51e.log')
    if log.exists():
        txt = log.read_text(encoding='utf-8', errors='replace')
        n429 = txt.count('429')
        npoll = txt.count('poll')
        print(f"  日志中 '429' 出现 {n429} 次；'poll' {npoll} 次（启动至今）")


if __name__ == '__main__':
    main()
