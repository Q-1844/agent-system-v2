# _34_fable_pilot/_34_build_gallery.py — 34.md §17 FABLE_PILOT_V1_GALLERY.html。
# 读 candidates.jsonl + shots 快照，按 Shot 1→8 排列；每镜：叙事目的/描述/实际
# prompt/参考/mode/候选 A·B(·C) 播放器/时长/音频/provenance/机器初筛/human 状态。
# 相对路径引用 ../projects/.../output/videos/*.mp4，file:// 直接可看。
import html
import io
import json
import sys
import subprocess
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
OUT = HERE / "FABLE_PILOT_V1_GALLERY.html"

SHOT_META = {
    "s01_c01": ("Shot 1 — Establishing", "葡萄园建立镜（地点/藤蔓/葡萄；狐狸未进主构图）",
                "scene reference"),
    "s02_c01": ("Shot 2 — Fox Enters", "狐狸沿路径穿过篱笆缺口进入（medium-wide，四足行走）",
                "fox cinematic identity reference (calibration R3)"),
    "s03_c01": ("Shot 3 — Discovery CU", "发现葡萄的特写表演（抬头/耳动/眼神聚焦）",
                "fox cinematic identity reference (calibration R3)"),
    "s04_c01": ("Shot 4 — Observation / Preparation", "观察葡萄、压低身体、蓄力（不塞多余动作）",
                "fox cinematic identity reference (calibration R3)"),
    "s05_c01": ("Shot 5 — The Jump", "全片动作最强：蓄力→单次跳跃→没够到→落地（不抓取）",
                "fox cinematic identity reference (calibration R3)"),
    "s06_c01": ("Shot 6 — Reaction", "落地恢复平衡，喘息/失望或不甘（不再跳）",
                "fox cinematic identity reference (calibration R3)"),
    "s07_c01": ("Shot 7 — Leave", "转身离开葡萄园核心区（全身/medium-wide）",
                "fox cinematic identity reference (calibration R3)"),
    "s08_c01": ("Shot 8 — Final Image", "葡萄仍在藤上，狐狸已远（空镜收尾寓意）",
                "scene reference"),
}


def ffprobe_audio(p: Path) -> dict:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_streams", str(p)], capture_output=True, text=True)
        d = json.loads(r.stdout or "{}")
        a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
        return {"audio": bool(a), "codec": (a or {}).get("codec_name"),
                "rate": (a or {}).get("sample_rate"), "ch": (a or {}).get("channels")}
    except Exception as e:
        return {"audio": False, "err": str(e)[:80]}


def main():
    cands = {}
    for line in (HERE / "candidates.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        cands.setdefault(r["shot_id"], []).append(r)

    parts = ["""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Fable Pilot V1 —《狐狸和葡萄》Gallery</title>
<style>
body{font-family:system-ui,'Segoe UI',sans-serif;background:#141414;color:#eee;max-width:1180px;margin:0 auto;padding:1.5rem}
h1{color:#ffd166}h2{color:#ffd166;margin-top:2.2rem;border-bottom:1px solid #333;padding-bottom:.3rem}
.meta{color:#aaa;font-size:.85rem}
.cand{background:#1e1e1e;border-radius:10px;padding:.9rem 1.1rem;margin:.8rem 0}
.cand h3{margin:.1rem 0 .5rem;font-size:1rem;color:#8ecae6}
video{width:100%;max-width:720px;border-radius:8px;background:#000}
table{border-collapse:collapse;font-size:.8rem;margin:.4rem 0}
td,th{border:1px solid #3a3a3a;padding:3px 8px;text-align:left;vertical-align:top}
.human{margin-top:.5rem;font-size:.9rem}
.human button{margin-right:6px;padding:2px 10px;border-radius:6px;border:1px solid #555;background:#222;color:#eee;cursor:pointer}
.note{width:100%;background:#111;color:#eee;border:1px solid #3a3a3a;border-radius:6px;padding:4px;margin-top:4px;font-family:inherit}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.75rem;margin-right:6px}
.pill.ok{background:#1b4332}.pill.warn{background:#5c4a00}.pill.bad{background:#5c1a1a}
details{margin-top:.4rem}summary{cursor:pointer;color:#8ecae6;font-size:.85rem}
pre{white-space:pre-wrap;word-break:break-word;background:#111;padding:8px;border-radius:6px;font-size:.75rem;color:#bbb}
</style></head><body>
<h1>Fable Pilot V1 —《狐狸和葡萄》8 镜候选 Gallery</h1>
<p class="meta">项目 Fable_Pilot_Fox_and_Grapes_V1 ｜ 模式 multi（单参考直生）｜ 狐狸参考=校准 R3（用户裁定）｜ 生成 2026-09-22 ｜ 34.md §17/§18</p>
<p class="meta">评价方法：逐镜观看每个候选，点 best/usable/reject 并写备注（写入本页本地 localStorage，另请把结论告知 Claude Code 记入 FEEDBACK）。关注：狐狸身份一致性 / 四足解剖 / 葡萄物体稳定性 / 背景纯净 / 音频（无 BGM+现场声）。</p>
"""]

    for shot_id in [f"s0{i}_c01" for i in range(1, 9)]:
        title, purpose, ref_desc = SHOT_META[shot_id]
        parts.append(f"<h2>{html.escape(title)}</h2>")
        parts.append(f"<p class='meta'>叙事目的：{html.escape(purpose)} ｜ 参考槽：{html.escape(ref_desc)}</p>")
        cl = cands.get(shot_id) or []
        if not cl:
            parts.append("<p class='meta'>（无候选记录）</p>")
            continue
        for i, r in enumerate(cl):
            tag = chr(ord("A") + i)
            local = r.get("local_recovered") or r.get("local") or ""
            rel = ("../_34_fable_pilot/recovered/" + Path(local).name) if r.get("local_recovered") else (Path(local).as_posix().replace("output/", "../projects/" + PROJ + "/output/") if local else "")
            fp = r.get("ffprobe") or {}
            aud = ffprobe_audio(Path(local)) if local else {}
            dur = fp.get("duration") or r.get("seconds") or "?"
            size = fp.get("size") or r.get("size") or "?"
            audio_pill = ("<span class='pill ok'>audio ✓ " + str(aud.get("codec", "")) + "</span>"
                          if aud.get("audio") else "<span class='pill bad'>audio ✗</span>")
            gb = r.get("generation_basis") or {}
            mode = gb.get("provider_mode") or "?"
            basis_fp = gb.get("visual_basis_fingerprint", "")
            parts.append(f"""<div class="cand">
<h3>Candidate {tag} <span class="meta">version {html.escape(str(r.get('version_id')))} · job {html.escape(str(r.get('job_id')))}</span></h3>
{audio_pill}<span class="pill ok">{html.escape(str(r.get('seconds')))}s</span><span class="pill ok">{html.escape(str(size))}</span><span class="pill ok">mode {html.escape(mode)}</span>
<br><video src="{html.escape(rel)}" controls preload="metadata"></video>
<table><tr><th>时长</th><th>尺寸</th><th>音频</th><th>参考资产</th><th>visual_basis_fp</th></tr>
<tr><td>{html.escape(str(dur))}</td><td>{html.escape(str(size))}</td>
<td>{html.escape(str(aud.get('codec') or '-'))} {html.escape(str(aud.get('rate') or ''))}ch{html.escape(str(aud.get('ch') or ''))}</td>
<td>{html.escape(str((r.get('reference_identity') or {}).get('hero_reference_asset_id') or (r.get('reference_identity') or {}).get('note') or 'scene'))}</td>
<td>{html.escape(str(basis_fp)[:14])}</td></tr></table>
<details><summary>实际 prompt（编译产物）</summary><pre>{html.escape(str(r.get('prompt_snapshot') or ''))}</pre></details>
<div class="human">人评：
<button onclick="setH(this,'{shot_id}',{i},'best')">⭐ best</button>
<button onclick="setH(this,'{shot_id}',{i},'usable')">✅ usable</button>
<button onclick="setH(this,'{shot_id}',{i},'reject')">❌ reject</button>
<span class="meta" id="st_{shot_id}_{i}"></span>
<textarea class="note" rows="1" placeholder="备注…" oninput="saveNote('{shot_id}',{i},this.value)"></textarea></div>
</div>""")
    parts.append("""
<p class="meta" style="margin-top:2rem">连续观看顺序 = Shot 1→8 每镜选一个候选交替播放即可感受整片连续性。</p>
<script>
function key(s,i){return 'fp34_'+s+'_'+i}
function setH(btn,s,i,v){localStorage.setItem(key(s,i),v);render(s,i)}
function saveNote(s,i,v){localStorage.setItem(key(s,i)+'_note',v)}
function render(s,i){const v=localStorage.getItem(key(s,i))||'';const n=document.getElementById('st_'+s+'_'+i);if(n)n.textContent=v?'【'+v+'】':'';const t=document.querySelector('[oninput^="saveNote(\\''+s+'\\','+i]');if(t&&v)t.value=localStorage.getItem(key(s,i)+'_note')||t.value}
document.querySelectorAll('[id^=st_]').forEach(e=>{const id=e.id.slice(3);render(id.split('_')[0]+'_'+id.split('_')[1], id.split('_')[2])});
</script>
</body></html>""")
    OUT.write_text("".join(parts), encoding="utf-8")
    print("gallery:", OUT, "| candidates:", sum(len(v) for v in cands.values()))


if __name__ == "__main__":
    main()
