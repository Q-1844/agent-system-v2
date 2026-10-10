# -*- coding: utf-8 -*-
"""Phase51 C：千张首帧战役分类预览页生成器。
按因子分类浏览（参考策略/景别/光线/融合提示词）+ 关键对比并排区 + 交互矩阵 +
评分分布；每张图带贴图感评分（视觉模型 1-5）。静态 HTML，相对路径引图。"""
import html
import io
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = Path("_p51_ff1000")
OUT = BASE / "index_ff1000.html"

rows = [json.loads(l) for l in (BASE / "screen.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
last = {}
for r in rows:
    if r.get("sticker") is not None:
        last[r["image"]] = r
scored = list(last.values())

by_img = {r["image"]: r for r in scored}


def parse(name):
    parts = name.split("__")[0].split("_")
    if len(parts) != 4:
        return None
    return {"ref": parts[0], "size": parts[1], "light": parts[2], "fusion": parts[3]}


imgs = []
for f in sorted((BASE / "images").glob("*.png")):
    meta = parse(f.name)
    if meta is None:
        continue
    sc = by_img.get(f.name)
    imgs.append({"file": f.name, **meta,
                 "sticker": sc.get("sticker") if sc else None,
                 "people": sc.get("people") if sc else None,
                 "hard": sc.get("hard_error") if sc else None})

REFS = ["无参考", "双Look", "双Look加场景", "单Look船长", "单Look大副"]
SIZES = ["MCU", "MS", "WS"]
LIGHTS = ["夕阳逆光", "柔和顺光"]
FUSIONS = ["基线", "强化融合"]

h = []
h.append("""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>千张首帧战役｜分类预览（贴图感研究）</title><style>
body{background:#111;color:#ddd;font-family:"Microsoft YaHei",sans-serif;margin:0;padding:24px}
h1{font-size:20px;color:#fff}h2{font-size:16px;color:#7fb3ff;border-bottom:1px solid #333;padding-bottom:6px;margin-top:36px}
h3{font-size:14px;color:#fff;margin:16px 0 8px}
.note{background:#1c2333;border-left:3px solid #7fb3ff;padding:10px 14px;font-size:13px;line-height:1.7;color:#bbc4d8}
table{border-collapse:collapse;font-size:13px;margin:10px 0}
td,th{border:1px solid #2a3040;padding:6px 12px;text-align:center}
th{background:#1c2333;color:#9fc3ff}
.best{color:#7ddf9a;font-weight:bold}.worst{color:#ff9a9a;font-weight:bold}
.thumb{background:#181c26;border:1px solid #2a3040;border-radius:6px;padding:8px;display:inline-block;margin:6px;vertical-align:top;width:280px}
.thumb img{width:100%;border-radius:4px}
.score{font-size:12px;margin-top:4px}
.s1{color:#7ddf9a}.s2{color:#b8e08a}.s3{color:#ecc78a}.s4{color:#ffa07a}.s5{color:#ff7a7a}
a{color:#7fb3ff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px}
</style></head><body>""")
h.append("<h1>千张首帧战役｜分类预览（贴图感研究，1020 张 / 60 条件）</h1>")
h.append("""<div class="note">核心课题：<b>人物贴图感</b>（1=完全融入 … 5=明显贴图；视觉模型逐张评分）。<br>
<b>头条发现</b>：无参考（强文本身份描述）贴图感全场最低 2.19 且硬错误/人数缺员最少——但牺牲跨镜身份一致；
图像参考每加一路，融合负担加重；场景图直贴最差组之一。景别：远景最贴图。<br>
<a href="index_takes.html">← 返回全批次审片页（视频成片与 65 版本）</a></div>""")

# ---- 交互矩阵 ----
cell = defaultdict(list)
for r in scored:
    m = parse(r["image"])
    if m is None:
        continue
    cell[(m["ref"], m["size"])].append(r["sticker"])
h.append("<h2>交互矩阵（贴图感均值，点击行内数字可跳到该组）</h2>")
h.append("<table><tr><th>参考\\景别</th>" + "".join(f"<th>{s}</th>" for s in SIZES) + "<th>组均</th></tr>")
for ref in REFS:
    vals = [statistics.mean(cell[(ref, s)]) if cell[(ref, s)] else None for s in SIZES]
    mean_all = statistics.mean([v for vs in vals if vs for v in [vs]] ) if any(vals) else None
    tds = []
    for i, v in enumerate(vals):
        if v is None:
            tds.append("<td>-</td>")
            continue
        cls = "best" if v == min(x for x in vals if x) else ("worst" if v == max(x for x in vals if x) else "")
        anchor = f"<a href='#g_{ref}_{SIZES[i]}'>{v:.2f}</a>" if cls else f"{v:.2f}"
        tds.append(f"<td class='{cls}'>{anchor}</td>")
    allv = [v for v in vals if v is not None]
    h.append(f"<tr><td>{ref}</td>{''.join(tds)}<td><b>{statistics.mean(allv):.2f}</b></td></tr>")
h.append("</table>")

# ---- 关键对比区（同条件跨参考策略并排）----
h.append("<h2>关键对比：同一镜头规格（MCU·柔和顺光·基线）下的参考策略差异</h2>")
h.append("<div class='grid'>")
for ref in REFS:
    cands = [r for r in imgs if r["ref"] == ref and r["size"] == "MCU"
             and r["light"] == "柔和顺光" and r["fusion"] == "基线" and r["sticker"]]
    cands.sort(key=lambda r: r["sticker"])
    pick = cands[0] if cands else None
    if pick:
        cls = f"s{pick['sticker']}"
        h.append(f"""<div class='thumb'><h3 style='margin:0'>{ref}</h3>
<img src='images/{pick['file']}'>
<div class='score {cls}'>贴图感 {pick['sticker']}（该组最低分样张）· 人数 {pick['people']} · 硬错误 {pick['hard']}</div></div>""")
h.append("</div>")

h.append("""<div class='note'>同一景别/光线/融合设置下：<b>无参考</b>的人物与海面船体天然融合；
<b>双 Look</b> 人物身份对齐参考但边缘可察；<b>双 Look 加场景</b>出现场景元素传导（图内多出缆桩等）。
这正是三角权衡的直观形态。</div>""")

# ---- 按参考策略分类浏览 ----
h.append("<h2>按参考策略浏览（每组 12 张带评分）</h2>")
for ref in REFS:
    h.append(f"<h3 id='g_{ref}_MCU'>参考策略：{ref}</h3>")
    h.append("<div class='grid'>")
    for size in SIZES:
        for light in LIGHTS:
            cands = [r for r in imgs if r["ref"] == ref and r["size"] == size
                     and r["light"] == light and r["sticker"]]
            if not cands:
                continue
            cands.sort(key=lambda r: r["sticker"])
            mid = cands[len(cands) // 2]   # 组内中位样张（最典型）
            cls = f"s{mid['sticker']}"
            h.append(f"""<div class='thumb'><h3 style='margin:0'>{size}·{light}·{mid['fusion']} <span class='score {cls}'>{mid['sticker']}分</span></h3>
<img src='images/{mid['file']}'>
<div class='score'>贴图感 {mid['sticker']} · 人数 {mid['people']} · 硬错误 {mid['hard']}（组内中位样张）</div></div>""")
    h.append("</div>")

# ---- 按景别浏览 ----
h.append("<h2>按景别浏览（各参考策略中位样张并排——看远景贴图感）</h2>")
for size in SIZES:
    h.append(f"<h3 id='g_景别_{size}'>景别：{size}</h3>")
    h.append("<div class='grid'>")
    for ref in REFS:
        cands = [r for r in imgs if r["ref"] == ref and r["size"] == size
                 and r["light"] == "柔和顺光" and r["sticker"]]
        if not cands:
            continue
        cands.sort(key=lambda r: r["sticker"])
        mid = cands[len(cands) // 2]
        cls = f"s{mid['sticker']}"
        h.append(f"""<div class='thumb'><h3 style='margin:0'>{ref} <span class='score {cls}'>{mid['sticker']}分</span></h3>
<img src='images/{mid['file']}'>
<div class='score'>柔和顺光·组内中位</div></div>""")
    h.append("</div>")

# ---- 60 条件 sheet 链接 ----
h.append("<h2>60 条件 Contact-Sheet（每条件 5 张缩略）</h2>")
sheet_dir = BASE / "sheets"
links = []
for f in sorted(sheet_dir.glob("*.jpg")):
    links.append(f"<a href='sheets/{f.name}'>{f.stem.replace('_', ' / ')}</a>")
h.append("<div class='note'>" + " · ".join(links) + "</div>")
h.append("</body></html>")

OUT.write_text("\n".join(h), encoding="utf-8")
print("written:", OUT, f"({len(scored)} scored images)")
