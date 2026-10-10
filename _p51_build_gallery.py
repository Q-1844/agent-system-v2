# -*- coding: utf-8 -*-
"""Phase51 C：看片页生成器——全部批次陈列版。
每镜的全部版本（P50 旧批/att2/att3/att4）带轮次与裁定标签；
废片区（排除镜）完整呈现所有批次。10-10 用户指令：废片全部要看。"""
import datetime
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REVIEW = Path("_p51_c_review")
TAKES = REVIEW / "takes"
OUT = REVIEW / "index_takes.html"

# 每镜裁定摘要（本轮一审终轮；来自执行侧逐镜抽帧审查）
VERDICTS = {
    "s01_c01": ("通过", "空船俯瞰建立镜（无人=符合）"),
    "s01_c02": ("通过", "双人甲板站位清楚"),
    "s01_c03": ("通过", "水下异动气泡"),
    "s01_c04": ("修复后通过", "初版（att2）人数+构图失败 → att3 起修复：大副单人前甲板警觉"),
    "s01_c05": ("高质量", "猛拉舵+目光交汇"),
    "s02_c01": ("通过·瑕疵", "逃离镜正确；船体偏灰白（跨镜漂移已知项）"),
    "s02_c02": ("通过·轻微", "船长正确；远景帆影存疑"),
    "s02_c03": ("通过", "大副固定缆绳（纹身一致）"),
    "s02_c04": ("通过·瑕疵", "怪物揭示+接力起点；att4 版背景帆船（att2 版无帆船但成片渲染时 final 指针已指向 att4——见版本别名缺陷）"),
    "s03_c01": ("通过", "接力承接"),
    "s03_c02": ("通过", "触须拍击"),
    "s03_c03": ("高质量", "大副被甩倒撑桅；att4 版 f2.5 有绿色网纹伪影（抽帧发现，单帧审看漏检——contact-sheet 抓到）"),
    "s03_c04": ("高质量", "船长死握舵（擦伤还原）"),
    "s04_c01": ("修复后通过", "初版（att2）触须包围无礁石（因果断裂）→ att3 起礁石入口出现"),
    "s04_c02": ("用户问题修复实证", "上轮大副站礁石 → 本轮全程在船上抛索"),
    "s04_c03": ("通过·轻微", "船长操作位（构图轻微漂移）"),
    "s04_c04": ("废片·排除", "虚构沙滩+船搁浅（att2/att3/att4 三版同类失败）——根因=首帧 attractor；首帧重建候选已备好待明日额度"),
    "s05_c01": ("通过", "大副舀水修整（人物在位）"),
    "s05_c02": ("高质量", "回望+港口灯+跨镜擦伤连续"),
    "s05_c03": ("废片·排除", "虚构到港两人上岸×3——根因=已选首帧本身是码头画面（i2v 忠实锚定）"),
}
IN_FILM = {"s01_c01", "s01_c02", "s01_c03", "s01_c04", "s01_c05", "s02_c01",
           "s02_c02", "s02_c03", "s02_c04", "s03_c01", "s03_c02", "s03_c03",
           "s03_c04", "s04_c01", "s04_c02", "s04_c03", "s05_c01", "s05_c02"}


def round_label(ts):
    if not ts:
        return "?", "gray"
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=8)))
    day = d.strftime("%m-%d")
    hm = d.strftime("%H:%M")
    if day == "10-09":
        return f"P50 批 ({hm})", "#8a7fb3"
    if hm < "04:30":
        return f"att2 批 ({hm})", "#7fb3ff"
    if hm < "07:00":
        return f"att3 批 ({hm})", "#7bdda0"
    return f"att4 批 ({hm})", "#ffd47f"


def main():
    from src.core.store import ProjectStore
    p = ProjectStore(Path("projects/P50_B")).load()
    h = []
    h.append("""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Phase51 C-T03 全批次审片｜成片 + 65 版本全陈列</title><style>
body{background:#111;color:#ddd;font-family:"Microsoft YaHei",sans-serif;margin:0;padding:24px}
h1{font-size:20px;color:#fff}h2{font-size:16px;color:#7fb3ff;border-bottom:1px solid #333;padding-bottom:6px;margin-top:40px}
h3{font-size:14px;color:#fff;margin:14px 0 8px}
.note{background:#1c2333;border-left:3px solid #7fb3ff;padding:10px 14px;font-size:13px;line-height:1.7;color:#bbc4d8}
.warn{background:#33261c;border-left:3px solid #e0a24a;padding:10px 14px;font-size:13px;line-height:1.7;color:#e8d5b8}
video{width:100%;max-width:520px;border-radius:6px;background:#000}
.card{background:#181c26;border:1px solid #2a3040;border-radius:8px;padding:12px;margin:10px 0}
.take{background:#141821;border:1px solid #262c3a;border-radius:6px;padding:8px;margin:8px 0}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;margin-right:6px}
.ok{background:#173a24;color:#7ddf9a}.hi{background:#1d3050;color:#8fbaff}
.issue{background:#4a3517;color:#ecc78a}.bad{background:#4a1c1c;color:#ff9a9a}
.gray{background:#333;color:#bbb}.meta{font-size:12px;color:#8892a6;line-height:1.6;margin-top:4px}
.shot{margin-bottom:6px}</style></head><body>""")
    h.append("<h1>Phase51 C-T03 全批次审片｜成片 + 65 版本全陈列</h1>")
    h.append("""<div class="note">run_36919eb5a073（COMPLETED）。每镜全部批次的版本都从 provider 拉回陈列
（本地路径是覆盖写，历史批次字节只在 provider 上——已全部下载）。<br>
<b>批次说明</b>：<b style="color:#8a7fb3">P50 批</b>=昨日交付版（旧基线）｜<b style="color:#7fb3ff">att2</b>=本轮首轮（一审第1轮的对象）｜
<b style="color:#7bdda0">att3</b>=一审驳回后修复轮｜<b style="color:#ffd47f">att4</b>=二审驳回后修复轮（=成片采用内容）。<br>
成片=18 镜 171.8s（s04_c04/s05_c03 排除）。裁切与正式选用归您。</div>""")
    h.append("""<div class="card"><h3>成片（171.8s 技术候选，待您看片）</h3>
<video controls src="final_film.mp4"></video></div>""")

    for sc in p.scenes:
        for sh in (sc.shots or []):
            sid = sh.shot_id
            verdict, note = VERDICTS.get(sid, ("?", ""))
            in_film = sid in IN_FILM
            cls = "bad" if "废片" in verdict else ("hi" if "高质量" in verdict or "修复" in verdict or "实证" in verdict else ("issue" if "瑕疵" in verdict or "轻微" in verdict else "ok"))
            h.append(f"<h2>{sid} <span class='tag {cls}'>{verdict}</span>"
                     f"{'<span class=\"tag hi\">成片采用</span>' if in_film else '<span class=\"tag bad\">未采用</span>'}</h2>")
            h.append(f"<div class='meta'>{note}</div>")
            vs = sorted((sh.video_versions or []), key=lambda v: v.created_at or 0)
            for vv in vs:
                label, color = round_label(vv.created_at)
                rel = f"takes/{sid}/{vv.version_id}.mp4"
                exists = (TAKES / sid / f"{vv.version_id}.mp4").exists()
                h.append(f"<div class='take'><h3>{sid} <span class='tag' style='background:{color};color:#111'>{label}</span> "
                         f"<span style='font-size:11px;color:#8892a6'>{vv.version_id}</span></h3>")
                if exists:
                    h.append(f"<video controls src='{rel}'></video>")
                else:
                    h.append(f"<div class='meta'>（本地文件缺失：{vv.version_id}）</div>")
                h.append("</div>")

    h.append("""<div class="warn"><b>版本别名缺陷说明</b>：本项目每镜所有版本共用一个本地 mp4 路径
（新版覆盖旧版字节），因此本页全部 65 个版本文件是从 provider URL 重新拉回的独立副本。
该缺陷已记入 FINAL_REPORT backlog（修复前"选旧版本"会拿到新字节——v2 跨版本选用不可信）。</div>""")
    h.append("</body></html>")
    OUT.write_text("\n".join(h), encoding="utf-8")
    print("written:", OUT)


if __name__ == "__main__":
    main()
