# -*- coding: utf-8 -*-
"""Phase 43 修复轮评审页 manifest 生成器 v3（两轮候选+三层+侧别图组）。

时间层：修复前（≤09-28）/ 一轮候选（09-30 上午）/ 二轮候选（09-30 13:12+）。
VideoVersion.local 覆盖写——版本真相在 provider URL，按 URL 下载缓存。
"""
import io
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROJ = ROOT / "projects" / "雨灯归航"
OUT = ROOT / "_43_repair"
MEDIA = OUT / "review_media"
MEDIA.mkdir(parents=True, exist_ok=True)

P = json.load(open(PROJ / "output" / "project.json", encoding="utf-8"))

CUTOFF1 = 1790731000.0     # 一轮开始（09-30 ~09:56）
CUTOFF2 = 1790740000.0     # 二轮开始（09-30 ~12:26；实际 13:12）

REPAIR_SHOTS = ["s01_c04", "s01_c05", "s01_c06", "s02_c02", "s02_c03",
                "s03_c01", "s03_c02", "s03_c03"]


def find_shot(sid):
    for sc in P.get("scenes", []):
        for sh in sc.get("shots", []):
            if sh.get("shot_id") == sid:
                return sh
    return None


def fetch(url: str, dst: Path) -> bool:
    if dst.exists() and dst.stat().st_size > 10000:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f)
        return True
    except Exception as e:
        print("  download failed:", url[:60], e)
        return False


def sample_frames(mp4: Path, stem: str) -> list:
    outs = []
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(mp4)], capture_output=True, text=True).stdout.strip() or 5.0)
    except Exception:
        dur = 5.0
    for tag, frac in (("f", 0.04), ("m", 0.5), ("e", 0.96)):
        dst = MEDIA / f"{stem}_{tag}.jpg"
        if not dst.exists():
            t = max(0.0, min(dur - 0.05, dur * frac))
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(mp4),
                            "-frames:v", "1", "-q:v", "3", str(dst)], capture_output=True)
        outs.append(f"review_media/{dst.name}" if dst.exists() else None)
    return outs


def vid_item(v, label):
    vid = v.get("version_id") or "x"
    stem = f"{label.replace(' ', '_')}_{vid[-6:]}"
    dst = MEDIA / f"{stem}.mp4"
    if not (v.get("url") and fetch(v["url"], dst)):
        return None
    return {
        "name": f"{label}（{vid[-8:]}）",
        "video_srcs": [{"label": label, "src": f"review_media/{dst.name}"}],
        "thumbs": sample_frames(dst, stem),
    }


groups = []
sel_qs = []
for sid in REPAIR_SHOTS:
    sh = find_shot(sid)
    if not sh:
        continue
    vvs = [v for v in (sh.get("video_versions") or []) if v.get("status") == "done"]
    pre = [v for v in vvs if (v.get("created_at") or 0) <= CUTOFF1]
    r1 = [v for v in vvs if CUTOFF1 < (v.get("created_at") or 0) <= CUTOFF2]
    r2 = [v for v in vvs if (v.get("created_at") or 0) > CUTOFF2]
    items = []
    if pre:
        it = vid_item(pre[-1], "对照：修复前现役版")
        if it:
            items.append(it)
    for v in r1:
        it = vid_item(v, "一轮候选")
        if it:
            items.append(it)
    for v in r2:
        it = vid_item(v, "二轮候选")
        if it:
            items.append(it)
    if not items:
        continue
    n_r1 = len([v for v in vvs if CUTOFF1 < (v.get("created_at") or 0) <= CUTOFF2])
    n_r2 = len([v for v in vvs if (v.get("created_at") or 0) > CUTOFF2])
    opts = ["保持对照（回滚）"]
    if n_r1:
        opts.append("一轮候选" + ("（多选一备注）" if n_r1 > 1 else ""))
    if n_r2:
        opts.append("二轮候选" + ("（多选一备注）" if n_r2 > 1 else ""))
    opts.append("都不用（重做）")
    groups.append({
        "id": sid,
        "title": f"{sid}（{sh.get('shot_size','')}，{sh.get('target_duration_s')}s）",
        "note": "先看对照，再按序看候选。判定：人物（白斑侧别/兜帽/黑豆眼/小巧）、画风（水彩）、"
                "道具（铜灯样式状态）、场景（空间/无凭空延伸）、动作与时长匹配、整体观感。",
        "videos": items,
    })
    sel_qs.append({
        "id": f"sel_{sid}", "group": sid,
        "title": f"{sid} 正式选片",
        "why": "选择后我执行 set-final+下游校验；选「多选一」时在备注写版本序号（如「二轮候选 2」）",
        "options": opts,
    })

# ---- 侧别图组（s02_c02 根帧二轮修复对照）----
look_path = PROJ / "output/resources/looks/look_295fc0cd2345/generations/gen_84b377c581c9/candidate_02.png"
images = []
if look_path.exists():
    dst = MEDIA / "patchside_look.png"
    if not dst.exists():
        shutil.copy2(look_path, dst)
    images.append({"name": "hero Look（基准：白斑=观众左侧）", "src": f"review_media/{dst.name}",
                   "note": "approved hero Look，白斑侧别的唯一基准"})
for stem, fn, note in (
        ("patchside_r1", "s02_c02_first_v1.png", "一轮根帧（侧别反——已否决，留作方差数据点）"),
        ("patchside_r2", None, None)):
    pass
# 一轮侧别反的两张在覆盖写中已丢（s02_c02_first_v1/v2 被二轮覆盖）——只有 Look 与二轮现行图可展示
r2_root = PROJ / "output/frames/s02_c02_first_v1.png"
if r2_root.exists():
    dst = MEDIA / "patchside_r2.png"
    shutil.copy2(r2_root, dst)
    images.append({"name": "二轮根帧（seed42，侧别✓，vision 审查 pass）", "src": f"review_media/{dst.name}",
                   "note": "当前选定根帧；白斑位置略高于 Look（耳侧），侧别正确"})

manifest = {
    "round": "repair43_v2",
    "round_title": "Phase 43 修复轮 · 两轮候选总评审（2026-09-30）",
    "round_date": "2026-09-30",
    "intro": "<b>修复轮候选评审 v2</b>：一轮（上午）后按你的指示「继续慢慢生成」补了二轮——"
             "①s02_c02 补种子修白斑侧别（新根帧 vision pass，视频+下游 c03 已重做）"
             "②s01_c06 补 0.5s 分拍对照（与 1s 分拍版并存）。"
             "每个分组：对照（修复前）→ 一轮候选 → 二轮候选。你的 ✅/⭐/❌ = 正式选片依据。",
    "image_groups": [
        {"id": "patchside", "title": "白斑侧别对照（s02_c02 根帧修复）", "images": images},
    ],
    "video_groups": groups,
    "questions": [
        {"id": "q_c06", "group": "s01_c06",
         "title": "s01_c06：凭空甲板是否消失？收束动作自然吗？",
         "why": "动作延伸+场景边界约束首轮正式验证"},
        {"id": "q_gran", "group": "s01_c06",
         "title": "c06 对照：1s 分拍（一轮）vs 0.5s 分拍（二轮）哪个更稳/更自然？",
         "why": "你提出的「描述与秒数对应关系」对照实测"},
        {"id": "q_patch", "group": "s02_c02",
         "title": "s02_c02 二轮根帧：侧别✓，但白斑位置略高（耳侧）——可接受吗？",
         "why": "侧别是硬判据，位置微差属方差；用户终审"},
        {"id": "q_s03", "group": "s03_c03",
         "title": "s03 链（c01/c02/c03）：回正效果与空间达标吗？c03 灯室结构（新 SIV）准确吗？",
         "why": "灯室内部 SIV 首次实战"},
        {"id": "q_rain",
         "title": "新根帧的雨丝（s01_c04/c06、s02 窗外有雨）可接受吗？",
         "why": "模型自发加雨——需要你定验收口径"},
        {"id": "q_lamp",
         "title": "铜灯亮态口径：候选多为亮灯（好评基线 EXP4 同样亮灯）；要不要强制 s01=未点亮？",
         "why": "剧情状态 vs 好评基线"},
    ] + sel_qs,
}

(OUT / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("manifest v3 written")
print("groups:", [(g["id"], len(g["videos"])) for g in groups])
print("patchside images:", len(images))
