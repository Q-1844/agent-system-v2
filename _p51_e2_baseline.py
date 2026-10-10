# 提取 C-T03（run_36919eb5a073）att2/att4 的 per-shot 提交/结算时间（E3 对照基线）
import json, sys, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from datetime import datetime

LED = Path('cache/p50_timing_ledger.jsonl')
rows = []
for l in LED.read_text(encoding='utf-8').splitlines():
    try:
        r = json.loads(l)
    except Exception:
        continue
    if r.get('project') != 'P50_B':
        continue
    if r.get('event') not in ('video_submitted', 'video_downloaded', 'video_failed'):
        continue
    rows.append(r)


def tp(s):
    s = str(s).replace('Z', '+00:00')
    return datetime.fromisoformat(s).timestamp()


# C-T03 视频窗口：2026-10-10 03:42 - 08:30（本地）
lo = datetime.fromisoformat('2026-10-10T03:42:00+08:00').timestamp()
hi = datetime.fromisoformat('2026-10-10T08:30:00+08:00').timestamp()
win = [r for r in rows if lo <= tp(r['utc']) <= hi]
print(f"C-T03 窗口事件 {len(win)} 条")
# att 边界（从事件时间戳近似）：att2 04:11-05:36 / att3 05:47-07:37 / att4 07:46-08:20
bounds = {
    'att1': (datetime.fromisoformat('2026-10-10T03:42:00+08:00').timestamp(),
             datetime.fromisoformat('2026-10-10T04:10:00+08:00').timestamp()),
    'att2': (datetime.fromisoformat('2026-10-10T04:11:00+08:00').timestamp(),
             datetime.fromisoformat('2026-10-10T05:36:00+08:00').timestamp()),
    'att3': (datetime.fromisoformat('2026-10-10T05:47:00+08:00').timestamp(),
             datetime.fromisoformat('2026-10-10T07:37:00+08:00').timestamp()),
    'att4': (datetime.fromisoformat('2026-10-10T07:46:00+08:00').timestamp(),
             datetime.fromisoformat('2026-10-10T08:20:00+08:00').timestamp()),
}
for att, (a, b) in bounds.items():
    w = [r for r in rows if a <= tp(r['utc']) <= b]
    subs = sorted(tp(r['utc']) for r in w if r['event'] == 'video_submitted')
    dls = [tp(r['utc']) for r in w if r['event'] == 'video_downloaded']
    fails = [r for r in w if r['event'] == 'video_failed']
    if not subs:
        print(f"{att}: 无提交"); continue
    span = (max(dls) - min(subs))/60 if dls else 0
    # 委派间隙
    ivs = []
    open_iv = None
    for r in sorted(w, key=lambda x: tp(x['utc'])):
        t = tp(r['utc'])
        if r['event'] == 'video_submitted':
            if open_iv is None:
                open_iv = t
        else:
            if open_iv is not None:
                ivs.append((open_iv, t)); open_iv = None
    covered = sum(e-s for s, e in ivs)
    idle = span*60 - covered if span else 0
    print(f"{att}: 提交 {len(subs)} 条（前3条相对首提交 +{subs[1]-subs[0]:.0f}s +{subs[2]-subs[0]:.0f}s）"
          f" 收货 {len(dls)} 失败 {len(fails)} | 墙钟 {span:.0f}min 在途并集 {covered/60:.0f}min 空闲 {idle/60:.0f}min")

# 11 镜同镜对照：每镜在 C-T03 的（att2/att4）结算时长
targets = ['s01_c01','s01_c02','s01_c03','s01_c04','s01_c05',
           's02_c01','s02_c02','s02_c03','s02_c04','s04_c04','s05_c03']
print("\n--- 11 镜在 C-T03 的生成时长（download - 最后一次同镜 submit）---")
for sid in targets:
    s_ts = [tp(r['utc']) for r in rows if r.get('shot_id') == sid and r['event'] == 'video_submitted' and lo <= tp(r['utc']) <= hi]
    d_ts = [tp(r['utc']) for r in rows if r.get('shot_id') == sid and r['event'] == 'video_downloaded' and lo <= tp(r['utc']) <= hi]
    if s_ts and d_ts:
        print(f"  {sid}: submits={len(s_ts)} dls={len(d_ts)} 最后结算时长={max(d_ts)-max(s_ts):.0f}s")
    else:
        print(f"  {sid}: submits={len(s_ts)} dls={len(d_ts)}")
