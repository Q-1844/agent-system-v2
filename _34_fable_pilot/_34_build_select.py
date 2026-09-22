# _34_fable_pilot/_34_build_select.py — 参考选择页（34.md Human Gate V1B）。
import html
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
d = json.load(open("projects/Fable_Pilot_Fox_and_Grapes_V1/output/resources.json",
                   encoding="utf-8"))
REVIEW = {
    "ref_bcfeac7ed0d9": "人评 pass（你已裁定）",
    "ref_d3cc470b09d6": "vision 3/3 pass",
    "ref_2ecaca2f3553": "identity✓ style⚠️（与R3同渲染）",
    "ref_a1b93821b4a6": "identity✓ style✓（最干净）",
    "ref_e5c261d74890": "identity✓ style✗",
    "ref_6bfdc88e57da": "identity⚠️ style✗",
    "ref_c19d9a0df2ec": "identity✓ style✗",
    "ref_8b3b3c13e9bc": "identity✗（正面视角身份漂移，慎选）style✓",
}
REFS = [
    ("R3 站姿 3/4（现 cover，校准资产）", "ref_bcfeac7ed0d9"),
    ("walk_side 行走侧视（四足迈步）", "ref_2ecaca2f3553"),
    ("crouch_ready 低伏蓄力（准备跃起）", "ref_a1b93821b4a6"),
    ("sit_look_up 坐地仰望", "ref_e5c261d74890"),
    ("turned_away 背 3/4 离开", "ref_6bfdc88e57da"),
    ("alert_gaze 仰头警觉", "ref_c19d9a0df2ec"),
    ("stand_front 正面站立", "ref_8b3b3c13e9bc"),
    ("sit_fox 坐姿狐（姿态单一）", "ref_d3cc470b09d6"),
]
SHOTS = [("s02_c01", "Fox Enters 进入"), ("s03_c01", "Discovery CU 发现特写"),
         ("s04_c01", "Prepare 蓄力"), ("s05_c01", "The Jump 跳跃"),
         ("s06_c01", "Reaction 反应"), ("s07_c01", "Leave 离开")]


def img(aid):
    a = d["reference_assets"].get(aid) or {}
    loc = (a.get("local") or "").replace("\\", "/")
    return f"/{loc}" if loc else ""


parts = ["""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>狐狸参考库 — 按镜选择</title>
<style>body{font-family:system-ui;background:#141414;color:#eee;max-width:1150px;margin:0 auto;padding:1.5rem}
h1{color:#ffd166}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.card{background:#1e1e1e;border-radius:10px;padding:8px}.card img{width:100%;border-radius:6px}
.card h3{font-size:.85rem;margin:.4rem 0}.card p{font-size:.7rem;color:#aaa;margin:.1rem 0}
.shot{background:#1e1e1e;border-radius:10px;padding:10px 14px;margin:10px 0;display:flex;align-items:center;gap:14px}
select{background:#222;color:#eee;border:1px solid #555;border-radius:6px;padding:6px;font-size:.9rem;min-width:300px}
button{background:#ffd166;color:#111;border:0;border-radius:8px;padding:8px 18px;font-weight:600;cursor:pointer}
#summary{background:#111;border:1px solid #3a3a3a;border-radius:8px;padding:10px;white-space:pre-wrap;font-size:.85rem;color:#8ecae6;margin-top:1rem}
</style></head><body>
<h1>狐狸姿态参考库 — 按镜选择（34.md Human Gate V1B）</h1>
<p>每个有狐狸的镜头选择其"开场参考"。当前默认都是 R3 站姿（这就是每镜开场一样的原因）。
选完点"生成汇总"，把汇总文本发给 Claude Code。</p>
<div class="grid">"""]
for label, aid in REFS:
    badge = REVIEW.get(aid, "")
    color = "#1b4332" if "✓（" in badge or "pass（" in badge else ("#5c4a00" if "⚠️" in badge else "#5c1a1a")
    parts.append(f'<div class="card"><img src="{html.escape(img(aid))}">'
                 f'<h3>{html.escape(label)}</h3><p>{aid}</p>'
                 f'<p style="color:{color}">{html.escape(badge)}</p></div>')
parts.append('</div><h2>按镜选择</h2>')
opts = "".join(f'<option value="{aid}">{html.escape(label)}</option>'
               for label, aid in REFS)
for sid, slabel in SHOTS:
    parts.append(f'<div class="shot"><b style="min-width:200px">{sid} — {slabel}</b>'
                 f'<select id="{sid}">{opts}</select></div>')
parts.append('''<p>s01/s08 为纯环境镜（场景参考），无需选择。</p>
<button onclick="gen()">生成汇总</button><div id="summary">（未生成）</div>
<script>
function gen(){
 let lines=[];
 for(const id of ["s02_c01","s03_c01","s04_c01","s05_c01","s06_c01","s07_c01"]){
   const v=document.getElementById(id).value;
   const label=document.getElementById(id).selectedOptions[0].textContent;
   lines.push(id+" -> "+label+" ("+v+")");
   localStorage.setItem('ref_'+id,v);
 }
 document.getElementById('summary').textContent=lines.join("\\n");
}
for(const id of ["s02_c01","s03_c01","s04_c01","s05_c01","s06_c01","s07_c01"]){
 const v=localStorage.getItem('ref_'+id); if(v) document.getElementById(id).value=v;
}
</script></body></html>''')
Path("_34_fable_pilot/REFERENCE_SELECT.html").write_text("".join(parts), encoding="utf-8")
print("selection page written")
