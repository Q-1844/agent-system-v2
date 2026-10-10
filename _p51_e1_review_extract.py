# Phase51-E 一审抽帧：11 镜新版本下载→ffmpeg 5 点抽帧→contact sheet 拼图
import json, sys, io, subprocess, urllib.request
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, '.')
from src.core.store import ProjectStore

VERSIONS = {
    's01_c01': 'vv_7279681a', 's01_c02': 'vv_e29e2324', 's01_c03': 'vv_928f0dd2',
    's01_c04': 'vv_0fa43f87', 's01_c05': 'vv_42af3fbc', 's02_c01': 'vv_f2d13d41',
    's02_c02': 'vv_b6b3d28a', 's02_c03': 'vv_c44e4ec8', 's02_c04': 'vv_4b485eb9',
    's04_c04': 'vv_ad8b0513', 's05_c03': 'vv_433de2e5',
}
OUT = Path('_p51_e_review')
OUT.mkdir(exist_ok=True)
FRAMES = ['f0.jpg', 'f2.5.jpg', 'f5.jpg', 'f7.5.jpg', 'f9.5.jpg']

proj = ProjectStore(Path('projects/P50_B')).load()

def url_of(shot_id, vid):
    sh = proj.get_shot(shot_id)
    for v in (sh.video_versions or []):
        if v.version_id == vid:
            return v.url or v.local
    return None

# 1) 下载+抽帧
for sid, vid in VERSIONS.items():
    d = OUT / sid
    d.mkdir(exist_ok=True)
    if (d / 'f9.5.jpg').exists():
        continue
    src = url_of(sid, vid)
    assert src, f'{sid} {vid} 无 URL'
    mp4 = d / 'v.mp4'
    if not mp4.exists():
        if str(src).startswith('http'):
            urllib.request.urlretrieve(src, mp4)
        else:
            import shutil
            shutil.copy(src, mp4)
    # 时长
    dur = float(subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', str(mp4)], capture_output=True, text=True).stdout.strip())
    pts = [0, dur*0.25, dur*0.5, dur*0.75, max(0.1, dur-0.4)]
    for name, t in zip(FRAMES, pts):
        subprocess.run(['ffmpeg', '-y', '-ss', str(t), '-i', str(mp4),
                        '-frames:v', '1', '-q:v', '3', str(d / name)],
                       capture_output=True)
    print(sid, vid, f'dur={dur:.1f}s frames ok')

# 2) contact sheet（3 镜/张 × 4 张）
TH_W = 300
TH_H = int(TH_W * 9 / 16)
LABEL = 26
sids = list(VERSIONS)
per = 3
for si in range(0, len(sids), per):
    chunk = sids[si:si+per]
    W = 5 * TH_W + 6 * 6
    H = len(chunk) * (TH_H + LABEL + 6) + 6
    sheet = Image.new('RGB', (W, H), (18, 20, 28))
    dr = ImageDraw.Draw(sheet)
    for ri, sid in enumerate(chunk):
        y = 6 + ri * (TH_H + LABEL + 6)
        dr.text((8, y + 2), f"{sid}  {VERSIONS[sid]}", fill=(255, 220, 120))
        for ci, f in enumerate(FRAMES):
            p = OUT / sid / f
            x = 6 + ci * (TH_W + 6)
            if p.exists():
                im = Image.open(p).resize((TH_W, TH_H))
                sheet.paste(im, (x, y + LABEL))
            else:
                dr.rectangle([x, y + LABEL, x + TH_W, y + LABEL + TH_H], fill=(60, 0, 0))
        dr.text((8 + 5 * (TH_W + 6) - 90, y + LABEL + 4), '', fill=(120, 120, 120))
    out_p = OUT / f'sheet_{si//per + 1}.jpg'
    sheet.save(out_p, quality=88)
    print('sheet:', out_p)
