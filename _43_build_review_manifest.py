# -*- coding: utf-8 -*-
"""Phase 43 修复轮评审页 manifest 生成器 v2。

关键事实：VideoVersion.local 全部指向同一覆盖写文件（磁盘只有最新代）——
版本真相在 provider URL（公网读，不过期）。本版全部按 URL 下载，按 version_id
缓存。s03_c01/c02 各有 2 个新变体，全部列出。
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

REPAIR_SHOTS = ["s01_c04", "s01_c05", "s01_c06", "s02_c02", "s02_c03",
                "s03_c01", "s03_c02", "s03_c03"]
CUTOFF = 1790731000.0


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


groups = []
for sid in REPAIR_SHOTS:
    sh = find_shot(sid)
    if not sh:
        continue
    vvs = sh.get("video_versions") or []
    # 对照=修复轮开始前最新的 done 版本（生成处理器会自动把新版本设为 final——
    # 所以 final 指针已前移，不能用它找对照；按 created_at 取上一代）
    prior = [v for v in vvs if (v.get("created_at") or 0) <= CUTOFF
             and v.get("status") == "done"]
    old = prior[-1] if prior else None
    new = [v for v in vvs if (v.get("created_at") or 0) > CUTOFF
           and v.get("status") == "done"]
    items = []
    if old and old.get("url"):
        stem = f"{sid}_old_{(old.get('version_id') or 'x')[-6:]}"
        dst = MEDIA / f"{stem}.mp4"
        if fetch(old["url"], dst):
            items.append({
                "name": f"对照：修复前现役版（{(old.get('version_id') or '')[-8:]}）",
                "video_srcs": [{"label": f"{sid} 修复前", "src": f"review_media/{dst.name}"}],
                "thumbs": sample_frames(dst, stem),
            })
    for i, v in enumerate(new):
        vid = v.get("version_id") or f"v{i}"
        stem = f"{sid}_new_{vid[-6:]}"
        dst = MEDIA / f"{stem}.mp4"
        if v.get("url") and fetch(v["url"], dst):
            items.append({
                "name": f"新候选 {i+1}（{vid[-8:]}）",
                "video_srcs": [{"label": f"{sid} 新候选", "src": f"review_media/{dst.name}"}],
                "thumbs": sample_frames(dst, stem),
            })
    if not items:
        print("!! no items for", sid)
        continue
    groups.append({
        "id": sid,
        "title": f"{sid}（{sh.get('shot_size','')}，{sh.get('target_duration_s')}s）",
        "note": "先看对照再看新候选。判定：人物（白斑侧别/兜帽向下/黑豆眼/小巧可爱）、画风（水彩）、"
                "道具（铜灯样式与状态）、场景（空间正确/无凭空延伸）、动作与时长匹配、整体观感。",
        "videos": items,
    })

manifest = {
    "round": "repair43",
    "round_title": "Phase 43 修复轮 · 《雨灯归航》偏差链正式候选（2026-09-30）",
    "round_date": "2026-09-30",
    "intro": "<b>修复轮候选评审</b>：偏差根（s01_c04/s02_c02）按定版配方（crop 根帧+prop 绑定+双参考接力）重做，"
             "下游沿真实依赖重做/复核（系统 Agent 按 Skill v1.2 自主选择，trace 在档）。"
             "每个分组=先看对照（修复前现役版），再看新候选。你的选择=正式选片依据；选择后执行 set-final+下游校验。"
             "<br><b>已知事项</b>：①新根帧 s01_c04 画面有雨丝（对照版无）②s02_c02 两个根帧候选的白斑侧别都反了（Look=观众左侧）"
             "③s01_c06 用时间戳分拍动作描述（1s 粒度），0.5s 细粒度对照未做（是否补测？）④铜灯在多个候选里是亮的（s01 剧情状态=未修；但用户好评基线同样亮灯）。",
    "image_groups": [],
    "video_groups": groups,
    "questions": [
        {"id": "q_overall", "title": "总判：哪些镜的新候选可以正式替换？（逐组 ✅可用/⭐首选/❌不可用 + 备注）",
         "why": "正式选片依据"},
        {"id": "q_c06", "title": "s01_c06：凭空甲板是否消失？收束动作自然吗？",
         "why": "动作延伸+场景边界约束首轮正式验证"},
        {"id": "q_rain", "title": "新根帧的雨丝（s01_c04/c06、s02 窗外有雨）可接受吗？",
         "why": "模型自发加雨——需要你定验收口径"},
        {"id": "q_lamp", "title": "铜灯状态口径：新候选灯多为亮态（好评基线 EXP4 同样亮灯）；要不要强制 s01=未点亮状态？",
         "why": "剧情状态 vs 好评基线的口径裁定"},
        {"id": "q_gran", "title": "c06 时间戳分拍描述（1s 粒度）观感是否更稳？要不要补 0.5s 细粒度对照（你提的方向）？",
         "why": "「描述与秒数对应关系」首轮实测"},
        {"id": "q_patch", "title": "s02_c02 两个根帧候选白斑侧别都反——是否补生成更多种子直到侧别正确（再重做 s02_c02/c03）？",
         "why": "用户验收判据：白斑侧别=Look 同侧；EXP3 同样记录过此方差"},
        {"id": "q_s03", "title": "s03 链回正效果与空间达标吗？c03 灯室结构（新 SIV）准确吗？",
         "why": "灯室内部 SIV 首次实战"},
    ],
}

(OUT / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("manifest written")
print("groups:", [(g["id"], len(g["videos"])) for g in groups])
