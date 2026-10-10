# V-A 抽帧判定：每条抽 f0/f4/f7 → contact sheet（方位命中目检）
import json, sys, io, subprocess, urllib.request, time
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path('_p51_va_review')
OUT.mkdir(exist_ok=True)

rows = {}
for l in open('cache/p50_timing_ledger.jsonl', encoding='utf-8'):
    try:
        e = json.loads(l)
    except Exception:
        continue
    if e.get('event') == 'va_multiview_video' and e.get('ok'):
        rows[e['shot_id']] = e['url']   # 同 tag 取最后一次

done = sorted(rows)
print(f'抽帧 {len(done)} 条')
mp4s = {}
for tag in done:
    d = OUT / tag
    d.mkdir(exist_ok=True)
    mp4 = d / 'v.mp4'
    if not mp4.exists():
        for i in range(4):
            try:
                data = urllib.request.urlopen(rows[tag], timeout=120).read()
                mp4.write_bytes(data)
                break
            except Exception as ex:
                print(tag, 'dl retry', i, type(ex).__name__)
                time.sleep(10)
        else:
            continue
    mp4s[tag] = mp4

TH_W, TH_H, LABEL = 200, 112, 20
tags = sorted(mp4s)
per = 5
for si in range(0, len(tags), per):
    chunk = tags[si:si+per]
    W = 3 * TH_W + 4 * 6
    H = len(chunk) * (TH_H + LABEL + 6) + 6
    sheet = Image.new('RGB', (W, H), (18, 20, 28))
    dr = ImageDraw.Draw(sheet)
    for ri, tag in enumerate(chunk):
        y = 6 + ri * (TH_H + LABEL + 6)
        dr.text((8, y + 2), tag, fill=(150, 220, 255))
        mp4 = mp4s[tag]
        dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                                    'format=duration', '-of', 'csv=p=0', str(mp4)],
                                   capture_output=True, text=True).stdout.strip() or 8)
        for ci, t in enumerate([0.2, dur*0.5, dur-0.5]):
            fr = d_ = OUT / tag / f'f{ci}.jpg'
            subprocess.run(['ffmpeg', '-y', '-ss', str(t), '-i', str(mp4),
                            '-frames:v', '1', '-q:v', '4', str(fr)], capture_output=True)
            x = 6 + ci * (TH_W + 6)
            if fr.exists():
                im = Image.open(fr).resize((TH_W, TH_H))
                sheet.paste(im, (x, y + LABEL))
    p = OUT / f'va_sheet_{si//per+1}.jpg'
    sheet.save(p, quality=85)
    print('sheet:', p)
