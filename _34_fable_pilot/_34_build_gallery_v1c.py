# _34_fable_pilot/_34_build_gallery_v1c.py — 34.md V1C 对比 Gallery。
# 每镜三层：V1C 尾帧接力（新）/ V1B 姿态参考 / R1 首轮（折叠），+ 衔接量化数据。
import html
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
OUT = HERE / "FABLE_PILOT_V1C_GALLERY.html"

ORDER = ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]
RELAY_NOTE = {
    "s02_c01": "参考=s01 尾帧（空园土路）——狐狸从画外入画",
    "s03_c01": "参考=s02 尾帧（行走中）",
    "s04_c01": "参考=s03 尾帧（CU 状态）",
    "s05_c01": "参考=s04 尾帧（蓄力结束态）",
    "s06_c01": "参考=s05 尾帧（落地状态）",
    "s07_c01": "参考=s06 尾帧（坐地仰望态）",
}
V1B_ASSIGN = {
    "s02_c01": "walk_side", "s03_c01": "alert_gaze", "s04_c01": "crouch_ready",
    "s05_c01": "crouch_ready", "s06_c01": "sit_look_up", "s07_c01": "turned_away",
}
CONT = {
    "s03_c01": 28.1, "s04_c01": 3.5, "s05_c01": 9.0, "s06_c01": 4.3, "s07_c01": 3.3,
}
AUDIO = None


def vid(src):
    return f'<video src="{html.escape(src)}" controls preload="metadata"></video>'


def main():
    v1c = {}
    for line in (HERE / "v1c_runs.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("job_status") == "done":
            v1c[r["shot_id"]] = r
    audio = json.loads((HERE / "v1c_audio_check.json").read_text(encoding="utf-8")) \
        if (HERE / "v1c_audio_check.json").exists() else {}
    r1 = {}
    for line in (HERE / "candidates.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        r1.setdefault(r["shot_id"], []).append(r)

    parts = ["""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Fable Pilot V1C — 尾帧接力对比</title>
<style>body{font-family:system-ui;background:#141414;color:#eee;max-width:1150px;margin:0 auto;padding:1.5rem}
h1{color:#ffd166}h2{color:#ffd166;margin-top:2rem}.meta{color:#aaa;font-size:.85rem}
.cand{background:#1e1e1e;border-radius:10px;padding:.9rem;margin:.8rem 0}
video{width:100%;max-width:700px;border-radius:8px;background:#000}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.75rem;margin-right:6px;background:#1b4332}
.pill.warn{background:#5c4a00}
details{margin-top:.5rem}summary{cursor:pointer;color:#8ecae6;font-size:.85rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
</style></head><body>
<h1>Fable Pilot V1C — 尾帧接力 vs 姿态参考 vs 第一轮</h1>
<p class="meta">尾帧接力=参考用上一镜实际视频末帧（开场衔接上一镜结尾，动作由 prompt 驱动）。
衔接量化（上一镜末帧 vs 本镜首帧 diff）：s03=28.1 / s04=3.5 / s05=9.0 / s06=4.3 / s07=3.3（低=衔接紧）。</p>
"""]
    for sid in ORDER:
        v = v1c.get(sid) or {}
        a = audio.get(sid) or {}
        bgm = (a.get("musical_2_3", 0) > 0.15 or a.get("musical_3_3", 0) > 0.15
               or a.get("musical_1_3", 0) > 0.15)
        bgm_pill = ("<span class='pill warn'>⚠️BGM信号（后段）</span>" if bgm
                    else "<span class='pill'>音频无BGM信号</span>")
        parts.append(f"""<h2>{sid} — V1C 尾帧接力</h2>
<p class="meta">{html.escape(RELAY_NOTE[sid])} ｜ 衔接 diff={CONT.get(sid,'—')} ｜ {bgm_pill}</p>
<div class="cand"><span class="pill">V1C 尾帧接力</span> <span class="meta">{html.escape(str(v.get('version_id')))}</span>
<br>{vid(f"../_34_fable_pilot/recovered_v1c/{sid}_V1C.mp4")}</div>
<details open><summary>V1B 姿态参考版（对比：开场={html.escape(V1B_ASSIGN[sid])}）</summary>
<div class="cand">{vid(f"../_34_fable_pilot/recovered_v1b/{sid}_V1B.mp4")}</div></details>
<details><summary>第一轮候选（开场=R3 站姿）</summary><div class="row">""")
        for i, r in enumerate(r1.get(sid) or []):
            tag = chr(ord("A") + i)
            parts.append(f"""<div class="cand"><span class="pill">R1 {tag}</span>
{vid(f"../_34_fable_pilot/recovered/{sid}_{tag}.mp4")}</div>""")
        parts.append("</div></details>")
    parts.append("""<p class="meta">本轮评价重点：①尾帧接力的镜间衔接（连续播放看）②接力链上狐狸身份是否漂移（逐代条件化风险）③
每个衔接点的构图是否被尾帧拖死（发挥空间）④音频。点名问题组发 Claude Code，只重做该组。</p>
</body></html>""")
    OUT.write_text("".join(parts), encoding="utf-8")
    print("v1c gallery:", OUT)


if __name__ == "__main__":
    main()
