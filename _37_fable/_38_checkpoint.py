# _37_fable/_38_checkpoint.py — 《狐狸进村》人评页生成（从 _38_trace_run.json 重建）。
# 媒体本地化：output/videos/{shot_id}.mp4 + output/frames/{shot_id}_video_end.jpg
#   → media/M{n}.mp4 / M{n}_end.jpg（相对路径，:8781 直接托管）。
# 用法: python _38_checkpoint.py   （每镜 submit/record 后重跑刷新页面）
import io
import json
import shutil
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _38_story import SHOTS  # noqa: E402

HERE = Path(__file__).parent
ROOT = HERE.parent
PROJ = ROOT / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"
TRACE = HERE / "_38_trace_run.json"
MEDIA = HERE / "media"


def localize(nkey, shot_id):
    src_v = PROJ / "output" / "videos" / f"{shot_id}.mp4"
    src_f = PROJ / "output" / "frames" / f"{shot_id}_video_end.jpg"
    dst_v = MEDIA / f"{nkey}.mp4"
    dst_f = MEDIA / f"{nkey}_end.jpg"
    if src_v.exists():
        shutil.copy2(src_v, dst_v)
    if src_f.exists():
        shutil.copy2(src_f, dst_f)
    # 首帧提取（接缝可视化：本镜开场 = 上镜收束？）
    dst_s = MEDIA / f"{nkey}_start.jpg"
    if dst_v.exists():
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(dst_v),
                        "-frames:v", "1", "-q:v", "3", str(dst_s)],
                       check=False, timeout=60)
    return dst_v.name, dst_f.name, dst_s.name


def mae_gray(a_path, b_path, w=128, h=128):
    from PIL import Image
    ia = Image.open(a_path).convert("L").resize((w, h))
    ib = Image.open(b_path).convert("L").resize((w, h))
    pa, pb = list(ia.getdata()), list(ib.getdata())
    return round(sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa), 1)


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


BRIDGES = {"M3B": "M4", "M5B": "M6"}   # 桥 key → 后继镜 key（播放序插在后继之前）


def _bridge_card(r, vname, fname, sname, seam_mae):
    """keyframes 桥卡片（双端钉死：首槽=上镜尾帧、尾槽=后继镜首帧）。"""
    shot_id = r["shot_id"]
    mae_t = r.get("seam_bridge_tail_to_m6_first") or r.get("mae_bridge_tail_vs_target")
    seam = f"<b>{seam_mae}</b>" if seam_mae is not None else "-"
    return f"""
<div class="card" id="M3B" style="border-color:#8b4513;background:#fffdf5">
  <h3>桥 M3B = {shot_id} <span class="tag none">人工: {r.get('human_deviation_level')}</span>
      <span class="tag none">keyframes_bridge</span>
      <span class="tag {'major' if seam_mae and seam_mae>70 else ('minor' if seam_mae and seam_mae>35 else 'none')}">接缝(上镜尾→本镜首): {seam}</span></h3>
  <video controls preload="metadata" src="media/{vname}?v={V}"></video>
  <div class="cols">
    <div><h4>首帧（开场=上镜尾帧接力）</h4><img src="media/{sname}?v={V}"></div>
    <div><h4>末帧（着陆村庄，=M4f60 目标）</h4><img src="media/{fname}?v={V}"></div>
    <div>
      <h4>桥配置</h4><p class="cn">首槽=M3 尾帧（雨林边缘）｜尾槽=M4 视频第 60 帧（落到村口土路）｜keyframes 插值 10s</p>
      <h4>transition_description</h4><p>{esc(r.get('prompt_snapshot',''))}</p>
      <h4>MAE</h4><p>桥尾帧 vs 着陆目标: <b>{mae_t}</b>（37A 对标：同场景桥 2.9 / 多参考劫持 43.6）｜ vs M3 尾帧: {r.get('mae_bridge_tail_vs_m3_tail')}</p>
      <h4>验收</h4><p class="cn">{esc(r.get('human_reason',''))}</p>
      <h4>修复决策链 _repair_plan</h4><p class="cn">{esc(json.dumps(r.get('_repair_plan',{}), ensure_ascii=False))}</p>
    </div>
  </div>
</div>"""


def main():
    import time as _t
    global V
    V = str(int(_t.time()))
    rec = json.loads(TRACE.read_text(encoding="utf-8")) if TRACE.exists() else {}
    # 播放序（含桥）+ 接缝 MAE（上镜尾帧 vs 本镜首帧，128 灰度同刻度）
    order = []
    for spec in SHOTS:
        k = spec["shot"]
        for bk, succ in BRIDGES.items():
            if k == succ and bk in rec:
                order.append(bk)
        order.append(k)
    media = {}
    for k in order:
        r = rec.get(k)
        if not r:
            continue
        vname, fname, sname = localize(k, r["shot_id"])
        media[k] = (vname, fname, sname)
    seam = {}
    for i, k in enumerate(order):
        if i == 0 or k not in media:
            continue
        prev = order[i - 1]
        prev_end = MEDIA / media[prev][1]
        cur_start = MEDIA / media[k][2]
        seam[k] = mae_gray(prev_end, cur_start) \
            if prev_end.exists() and cur_start.exists() else None

    cards = []
    for spec in SHOTS:
        k = spec["shot"]
        for bk, succ in BRIDGES.items():
            if k == succ and bk in rec and bk in media:
                cards.append(_bridge_card(rec[bk], *media[bk], seam.get(bk)))
        r = rec.get(k)
        if not r or k not in media:
            continue
        shot_id = r["shot_id"]
        vname, fname, sname = media[k]
        va = r.get("vision_assessment") or {}
        sug = va.get("suggested_deviation", "-")
        human = r.get("human_deviation_level") or "待裁定"
        action = r.get("human_action") or "-"
        dims = ""
        for dk in ("expected_tail_match", "camera_framing_match", "lighting_consistency",
                   "narrative_action_match", "next_shot_readiness", "unexpected_elements"):
            if dk in va:
                dims += f"<div class='dim'><b>{dk}</b>: {esc(str(va.get(dk)))}</div>"
        mae_v = r.get("mae_prev")
        sm = seam.get(k)
        seam_html = (f"<b>{sm}</b>" if sm is not None else "-") \
            + (f"（提交时记录: {mae_v}）" if mae_v is not None and mae_v != sm else "")
        seam_tag_cls = 'major' if sm and sm > 70 else ('minor' if sm and sm > 35 else 'none')
        cards.append(f"""
<div class="card" id="{k}">
  <h3>{k} = {shot_id} <span class="tag {r.get('human_deviation_level') or 'pending'}">人工: {human}</span>
      <span class="tag {'major' if sug=='major' else ('minor' if sug=='minor' else 'none')}">vision建议: {sug}</span>
      <span class="tag {seam_tag_cls}">接缝(上镜尾→本镜首): {seam_html}</span></h3>
  <video controls preload="metadata" src="media/{vname}?v={V}"></video>
  <div class="cols">
    <div><h4>首帧（开场=上镜尾帧接力）</h4><img src="media/{sname}?v={V}"></div>
    <div><h4>末帧（实际尾帧）</h4><img src="media/{fname}?v={V}"></div>
    <div>
      <h4>预编剧</h4><p class="cn">{esc(spec['fields']['description'])}</p>
      <h4>expected_tail</h4><p class="cn">{esc(r.get('expected_tail',''))}</p>
      <h4>Vision 11维（仅证据）</h4>{dims or '<p>无</p>'}
      <h4>人工裁定</h4><p>{esc(action)} — {esc(r.get('human_reason',''))}</p>
      <h4>prompt hash</h4><p class="mono">{esc(str(r.get('prompt_hash','')))}</p>
    </div>
  </div>
</div>""")

    done = sum(1 for s in SHOTS if (rec.get(s["shot"]) or {}).get("human_deviation_level"))
    seam_line = " ｜ ".join(
        f"{order[i-1]}→{order[i]}: {seam.get(order[i])}"
        for i in range(1, len(order)) if order[i] in seam and seam.get(order[i]) is not None)
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<title>狐狸进村 — 38 第二轮检查点</title><style>
body{{font-family:"Microsoft YaHei",sans-serif;max-width:1100px;margin:24px auto;padding:0 12px;background:#faf7f2}}
h1{{color:#8b4513}} .card{{background:#fff;border:1px solid #e0d5c3;border-radius:10px;padding:16px;margin:18px 0;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
video{{width:100%;max-height:420px;background:#000;border-radius:6px}}
.cols{{display:flex;gap:18px;margin-top:12px;flex-wrap:wrap}} .cols>div{{flex:1;min-width:320px}}
img{{width:100%;border-radius:6px;border:1px solid #ddd}}
.tag{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:13px;margin-left:8px;color:#fff}}
.none{{background:#4caf50}} .minor{{background:#ff9800}} .major{{background:#f44336}} .pending{{background:#9e9e9e}}
.dim{{font-size:13px;color:#555;margin:2px 0}} .cn{{font-size:14px}} .mono{{font-family:monospace;font-size:12px;color:#777;word-break:break-all}}
.meta{{background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:12px;font-size:14px}}
</style></head><body>
<h1>《狐狸进村》— Phase 38 第二轮检查点</h1>
<div class="meta"><b>run</b>: {_38_runid()} ｜ <b>链根</b>: ref_d27e1a01c36f ｜ <b>裁定进度</b>: {done}/{len(SHOTS)}<br>
<b>接缝一览（上镜尾帧→本镜首帧 MAE，128 灰度；基线：同场景正常接力≈2 / 跨场景26-60 / 剧变构图90+）</b>:<br>{seam_line}<br>
播放序: M1→M2→M3→<b>M3B桥</b>→M4→M5→<b>M5B桥</b>→M6→M7→M8→M9（段C=s11 雨林小径 / 段D=s12 村庄村口）</div>
{''.join(cards) or '<p>暂无已生成镜头</p>'}
</body></html>"""
    out = HERE / "CHECKPOINT_38.html"
    out.write_text(html, encoding="utf-8")
    print(f"checkpoint page rebuilt: {out}（{done}/{len(SHOTS)} 裁定）")


def _38_runid():
    f = HERE / "_38_run_id.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "(未创建)"


if __name__ == "__main__":
    main()
