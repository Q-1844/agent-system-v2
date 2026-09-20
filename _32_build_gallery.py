# -*- coding: utf-8 -*-
"""32.md 评审页构建：EXPERIMENTS.jsonl → GALLERY.html（盲评默认，可揭示）。

用法：python _32_build_gallery.py
产物：_32_video_experiments/GALLERY.html（配合 _32_review_server.py :8778 或双击 file:// 离线用）
交互与 31.md 相同：点击卡片循环 未评→✅可用→⭐首选→❌不可用；备注逐条落 FEEDBACK.json；
localStorage 兜底（服务被杀/离线不丢，恢复自动合并）。
盲评：默认隐藏 shot/mode/prompt/评分，只显示组号+exp_id；「揭示信息」按钮切换。
"""
import html
import json
from collections import OrderedDict
from pathlib import Path

E32 = Path(__file__).resolve().parent / "_32_video_experiments"
RECORDS = E32 / "EXPERIMENTS.jsonl"
SCORES = E32 / "_scores_claude.json"

SHOT_ORDER = ["s17_c03", "s09_c01", "s09_c02", "s09_c08", "s10_c01", "s10_c03",
              "s11_c06", "s13_c01", "s13_c06", "s14_c01", "s14_c04", "s16_c05", "s17_c01"]

KF_CAVEATS = {
    "X040": "s13_c06 kf：首尾对瑕疵已声明——两帧均为全景（意图为手部特写），尾帧含克隆沈夜；输出若现克隆归因 REFERENCE",
    "X049": "s16_c05 kf：首尾对瑕疵已声明——首帧 2×柳衣、尾帧 2×柳衣+萧寒（克隆）；核心状态变化（持剑→跌坐断剑）成立",
    "X056": "s17_c01 kf：首尾对可用（站→跪开箱）；已知内景意图 vs 外景资产（D2 已知问题）",
}


def load_recs():
    recs = OrderedDict()
    for line in RECORDS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            recs[r["exp_id"]] = r
    return recs


def build():
    recs = load_recs()
    scores = json.loads(SCORES.read_text(encoding="utf-8")) if SCORES.exists() else {}
    by_shot = OrderedDict()
    for eid, r in recs.items():
        if r.get("status") not in ("done",):
            continue
        by_shot.setdefault(r["shot_id"], []).append(r)
    for k in by_shot:
        by_shot[k].sort(key=lambda r: r["exp_id"])

    shots = [s for s in SHOT_ORDER if s in by_shot] + \
            [s for s in by_shot if s not in SHOT_ORDER]

    parts = ["""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>32.md Video-First V1 评审</title>
<style>
body{font-family:system-ui,"Microsoft Yahei";background:#14161a;color:#dde;margin:0;padding:16px}
h2{color:#9cf;margin:22px 0 6px;font-size:17px}
.shotmeta{color:#889;font-size:12px;margin-bottom:8px;white-space:pre-wrap}
.grid{display:flex;flex-wrap:wrap;gap:12px}
.card{background:#1d2127;border:1px solid #2c313a;border-radius:8px;padding:8px;width:340px;cursor:pointer;user-select:none}
.card video{width:100%;border-radius:4px;background:#000}
.card .eid{font-weight:600;font-size:13px;margin:4px 0}
.card .meta{font-size:11px;color:#99a;white-space:pre-wrap}
.card .score{font-size:11px;color:#7c7;margin-top:2px;white-space:pre-wrap}
.card .note{width:100%;margin-top:6px;background:#12151a;color:#ccd;border:1px solid #2c313a;border-radius:4px;font-size:11px;padding:3px}
.card.ok{border-color:#3a7} .card.best{border-color:#fc0;box-shadow:0 0 6px #fc08} .card.bad{border-color:#c33}
.badge{float:right;font-size:12px}
#toolbar{position:sticky;top:0;background:#14161aee;padding:8px;border-bottom:1px solid #333;z-index:9}
button{background:#2a3140;color:#dde;border:0;border-radius:6px;padding:6px 14px;margin-right:8px;cursor:pointer}
.hidden{display:none}
</style></head><body>
<div id="toolbar">
<button onclick="reveal()">揭示信息（当前：<b id="rv">盲评</b>）</button>
<button onclick="exportLocal()">导出本地选择</button>
<span id="cnt" style="color:#889;font-size:12px"></span>
</div>
<p style="color:#889;font-size:12px">点击卡片循环：未评 → ✅可用 → ⭐首选 → ❌不可用。运动/运镜请在播放中评价；抽帧接触表仅辅助。评价自动保存（服务在线落 FEEDBACK.json，离线存 localStorage）。</p>
"""]

    gidx = 0
    for shot in shots:
        rows = by_shot[shot]
        gidx += 1
        first = rows[0]
        parts.append(f"<h2>第 {gidx} 组</h2>")
        meta = (f"{len(rows)} 条视频 ｜ 生成批次 {sorted({r.get('batch','') for r in rows})} ｜ "
                f"提示词风格 {sorted({r.get('prompt_style','') for r in rows})}")
        parts.append(f'<div class="shotmeta hiddenR">{html.escape(meta)}</div>')
        parts.append('<div class="grid">')
        for r in rows:
            eid = r["exp_id"]
            sc = scores.get(eid)
            sctxt = ""
            if sc and sc.get("scores"):
                sctxt = "claude预评: " + " ".join(f"{k}={v}" for k, v in sc["scores"].items())
                if sc.get("severe"):
                    sctxt += "  ⚠" + ";".join(sc["severe"])[:80]
            cave = KF_CAVEATS.get(eid, "")
            refs_n = len(r.get("refs") or [])
            seed = r.get("seed")
            hidden_meta = (f"mode={r['mode']} style={r['prompt_style']} refs={refs_n} "
                           f"seed={seed if seed is not None else '无'} run{r.get('run_index')}"
                           f" motion={((r.get('motion_proxy') or {}).get('early_over_mid'))}")
            blind_label = f"{eid}"
            parts.append(f"""
<div class="card" data-eid="{eid}" data-shot="{shot}">
  <span class="badge"></span>
  <div class="eid blindR">{html.escape(blind_label)} ｜ <span class="shotname">{shot}</span></div>
  <video src="videos/{html.escape(Path(r['local_mp4']).name)}" controls preload="metadata" loop></video>
  <div class="meta hiddenR">{html.escape(hidden_meta)}{'<br>' + html.escape(cave) if cave else ''}</div>
  <div class="score hiddenR">{html.escape(sctxt)}</div>
  <input class="note hiddenR" placeholder="备注…" data-notekey="{eid}">
</div>""")
        parts.append("</div>")

    parts.append("""
<script>
const KEY='v32_feedback_v1';
let fb={};
try{fb=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
function cls(c){c.classList.remove('ok','best','bad');}
function applyCard(c){
  const st=fb[c.dataset.eid]?.state||'';
  cls(c); if(st==='ok')c.classList.add('ok'); if(st==='best')c.classList.add('best'); if(st==='bad')c.classList.add('bad');
  c.querySelector('.badge').textContent=st==='ok'?'✅可用':st==='best'?'⭐首选':st==='bad'?'❌不可用':'';
  if(fb[c.dataset.eid]?.note)c.querySelector('.note').value=fb[c.dataset.eid].note;
}
document.querySelectorAll('.card').forEach(c=>{
  applyCard(c);
  c.addEventListener('click',e=>{
    if(e.target.classList.contains('note')||e.target.tagName==='VIDEO')return;
    const cur=fb[c.dataset.eid]?.state||'';
    const nxt={'':'ok','ok':'best','best':'bad','bad':''}[cur];
    fb[c.dataset.eid]=Object.assign({},fb[c.dataset.eid],{state:nxt});
    applyCard(c); save(c.dataset.eid,nxt,null); count();
  });
  c.querySelector('.note').addEventListener('change',e=>{
    fb[c.dataset.eid]=Object.assign({},fb[c.dataset.eid],{note:e.target.value});
    save(c.dataset.eid,null,e.target.value);
  });
});
function save(eid,state,note){
  const body={};
  if(state!==null)body.key=eid,body.state=state;
  if(note!==null)body.note_key=eid,body.note=note;
  fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).catch(()=>{});
  localStorage.setItem(KEY,JSON.stringify(fb));
}
async function exportLocal(){ // 服务恢复后把 localStorage 合并回服务端
  for(const [eid,v] of Object.entries(fb)){
    if(v.state)await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:eid,state:v.state})}).catch(()=>{});
    if(v.note)await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note_key:eid,note:v.note})}).catch(()=>{});
  }
  alert('已尝试合并本地选择到服务端');
}
let blind=true;
function reveal(){blind=!blind;document.getElementById('rv').textContent=blind?'盲评':'已揭示';
  document.querySelectorAll('.hiddenR').forEach(e=>e.classList.toggle('hidden',blind));
  document.querySelectorAll('.shotname').forEach(e=>e.classList.toggle('hidden',blind));}
function count(){const n={};document.querySelectorAll('.card').forEach(c=>{const s=fb[c.dataset.eid]?.state||'';n[s]=(n[s]||0)+1});
  document.getElementById('cnt').textContent=Object.entries(n).map(([k,v])=>(k||'未评')+'='+v).join('  ');}
// 启动：先取服务端 FEEDBACK 合并（本地优先）
(async()=>{try{const r=await fetch('/api/feedback');const srv=await r.json();
  for(const [eid,v] of Object.entries((srv.selections||{})))
    fb[eid]=Object.assign({},fb[eid]||{},{state:v.state||''});
  for(const [eid,note] of Object.entries(srv.notes||{}))
    fb[eid]=Object.assign({},fb[eid]||{},{note:note});
}catch(e){} document.querySelectorAll('.card').forEach(applyCard); count();})();
</script></body></html>""")
    out = E32 / "GALLERY.html"
    out.write_text("".join(parts), encoding="utf-8")
    n = sum(len(v) for v in by_shot.values())
    print(f"GALLERY.html built: {n} videos / {len(shots)} shot groups -> {out}")


if __name__ == "__main__":
    build()
