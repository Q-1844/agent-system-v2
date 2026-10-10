# -*- coding: utf-8 -*-
"""Phase51 C：contact-sheet 审核工具——把逐镜 5 点抽帧拼成带标签的大图。

背景（C-T03 实证）：执行侧逐张 Read 看帧（100+ 张）耗时 ~50-70min 且审核在环
期间流水线停靠。拼成 contact-sheet（每张 4 镜 × 5 帧）后一次看 4 镜，
审阅时间 ~1/4，且一次 Read 调用看一整张。

用法：python _p51_contact_sheet.py <frames_root> <out_dir> [per_sheet=4]
  frames_root/ 下每镜一个目录 <shot_id>/f*.jpg（5 点抽帧命名）。
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw

FRAMES = ["f0.jpg", "f2.5.jpg", "f5.jpg", "f7.5.jpg", "f9.5.jpg"]
THUMB_W = 256   # 单帧缩略宽（16:9 → 高 144）
LABEL_H = 26


def make_sheet(shot_ids, frames_root, out_path):
    cols = len(FRAMES)
    rows = len(shot_ids)
    th = int(THUMB_W * 9 / 16)
    W = cols * THUMB_W + (cols + 1) * 6
    H = rows * (th + LABEL_H + 6) + 6
    sheet = Image.new("RGB", (W, H), (18, 20, 28))
    draw = ImageDraw.Draw(sheet)
    for r, sid in enumerate(shot_ids):
        y0 = 6 + r * (th + LABEL_H + 6)
        draw.text((10, y0 + 4), sid, fill=(140, 190, 255))
        for c, fname in enumerate(FRAMES):
            x0 = 6 + c * (THUMB_W + 6)
            f = Path(frames_root) / sid / fname
            if f.exists():
                im = Image.open(f).resize((THUMB_W, th))
                sheet.paste(im, (x0, y0 + LABEL_H))
            else:
                draw.rectangle([x0, y0 + LABEL_H, x0 + THUMB_W, y0 + LABEL_H + th],
                               fill=(40, 40, 46))
                draw.text((x0 + 8, y0 + LABEL_H + 8), "missing", fill=(200, 90, 90))
        draw.line([(6, y0 + LABEL_H + th + 3), (W - 6, y0 + LABEL_H + th + 3)],
                  fill=(50, 56, 70))
    sheet.save(out_path, quality=88)
    return out_path


def main():
    frames_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_p51_c_review")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else frames_root / "_sheets"
    per_sheet = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_dirs = sorted([d for d in frames_root.iterdir()
                        if d.is_dir() and not d.name.startswith("_")])
    made = []
    for i in range(0, len(shot_dirs), per_sheet):
        group = [d.name for d in shot_dirs[i:i + per_sheet]]
        out = out_dir / f"sheet_{i // per_sheet + 1:02d}.jpg"
        make_sheet(group, frames_root, out)
        made.append(str(out))
        print("sheet:", out, "|", ", ".join(group))
    print(f"total {len(made)} sheets for {len(shot_dirs)} shots")


if __name__ == "__main__":
    main()
