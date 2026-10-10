# 修复轮抽帧：s02_c04/s04_c04 新版本（att2）
import json, sys, io, subprocess, urllib.request
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, '.')
from src.core.store import ProjectStore

OUT = Path('_p51_e_review/repair')
OUT.mkdir(parents=True, exist_ok=True)
proj = ProjectStore(Path('projects/P50_B')).load()

NEW = {}
for sid in ('s02_c04', 's04_c04'):
    sh = proj.get_shot(sid)
    # 取最新版本（created_at 最大）
    vvs = sorted(sh.video_versions or [], key=lambda v: getattr(v, 'created_at', 0))
    v = vvs[-1]
    NEW[sid] = (v.version_id, v.url or v.local)
    print(sid, 'newest:', v.version_id, str(v.url or v.local)[:90])

FRAMES = ['f0.jpg', 'f2.5.jpg', 'f5.jpg', 'f7.5.jpg', 'f9.5.jpg']
for sid, (vid, src) in NEW.items():
    d = OUT / sid
    d.mkdir(exist_ok=True)
    mp4 = d / 'v.mp4'
    if not mp4.exists():
        urllib.request.urlretrieve(src, mp4)
    dur = float(subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', str(mp4)], capture_output=True, text=True).stdout.strip())
    for name, t in zip(FRAMES, [0, dur*0.25, dur*0.5, dur*0.75, max(0.1, dur-0.4)]):
        subprocess.run(['ffmpeg', '-y', '-ss', str(t), '-i', str(mp4),
                        '-frames:v', '1', '-q:v', '3', str(d / name)], capture_output=True)
    print(sid, vid, f'dur={dur:.1f}s')

TH_W = 300
TH_H = int(TH_W * 9 / 16)
LABEL = 26
sids = list(NEW)
W = 5 * TH_W + 6 * 6
H = len(sids) * (TH_H + LABEL + 6) + 6
sheet = Image.new('RGB', (W, H), (18, 20, 28))
dr = ImageDraw.Draw(sheet)
for ri, sid in enumerate(sids):
    y = 6 + ri * (TH_H + LABEL + 6)
    dr.text((8, y + 2), f"{sid}  {NEW[sid][0]}", fill=(255, 220, 120))
    for ci, f in enumerate(FRAMES):
        p = OUT / sid / f
        x = 6 + ci * (TH_W + 6)
        if p.exists():
            im = Image.open(p).resize((TH_W, TH_H))
            sheet.paste(im, (x, y + LABEL))
out_p = OUT / 'repair_sheet.jpg'
sheet.save(out_p, quality=88)
print('sheet:', out_p)
