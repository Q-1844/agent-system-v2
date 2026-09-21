# -*- coding: utf-8 -*-
"""33.md GALLERY 构建器：§一 矩阵对比（Shot A/B/C 行 × R0-R3 列）+ §二 提示词密度对比。

- 每格 = video 控件（loop muted controls）+ 点击循环状态（未评/✅/⭐/❌）+ 备注
- 矩阵每行「▶ 同步播放」：同行四条同时从头播放（肉眼连续对比参考格式差异）
- 状态/备注按 exp_id 记录，POST /api/feedback 落盘；localStorage 兜底
- 幂等：重跑覆盖（视频列表来自 EXPERIMENTS.jsonl 终态 done 记录）
"""
import json
from pathlib import Path

E33 = Path(__file__).resolve().parent / "_33_fox_calibration"
RECORDS = E33 / "EXPERIMENTS.jsonl"

SHOTS = {"A": "Shot A — Close-Up Performance（发现葡萄·抬头·耳动·CU）",
         "B": "Shot B — Full-Body Walk（沿小路·低角度跟拍·全身）",
         "C": "Shot C — Simple Jump（蓄力·单次跳·未够到·落地）"}
REFS = {"R0": "R0 无参考（t2v 文本事实编译）",
        "R1": "R1 白底角色板（系统现行标准）",
        "R2": "R2 中性视频身份参考",
        "R3": "R3 电影感身份参考"}
DENSITY = [("F012", "base（§六基础 prompt，≈P2 级）"),
           ("F013", "P1 最小意图"),
           ("F014", "P2 +时序结构"),
           ("F015", "P3 +单条定向约束（葡萄保持挂藤）")]


def load_done():
    latest = {}
    if RECORDS.exists():
        for line in RECORDS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "done":
                    latest[r["exp_id"]] = r
    return latest


def build():
    done = load_done()
    data = []
    for eid, r in sorted(done.items()):
        data.append({"exp_id": eid, "shot": r["shot"], "ref": r["ref_format"],
                     "style": r.get("prompt_style") or "base",
                     "mp4": f"videos/{Path(r['local_mp4']).name}",
                     "motion": (r.get("motion_proxy") or {}).get("early_over_mid")})
    data_js = json.dumps(data, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>33.md 狐狸校准 · 参考格式 × 提示词密度</title>
<style>
 body{font-family:"Microsoft YaHei",sans-serif;background:#141414;color:#ddd;margin:16px}
 h1{font-size:20px} h2{font-size:16px;margin-top:26px}
 .hint{color:#999;font-size:13px;margin:6px 0 14px}
 table{border-collapse:collapse;width:100%}
 th,td{border:1px solid #333;padding:6px;text-align:center;vertical-align:top}
 th{background:#1f1f1f;font-size:13px}
 td.shot{background:#1a1a1a;width:140px;font-size:12px;text-align:left}
 video{width:100%;max-width:320px;display:block;background:#000}
 .cell{cursor:pointer;user-select:none}
 .lbl{font-size:11px;color:#8ab4f8;margin:3px 0}
 .state{font-size:16px}
 .note{width:96%;font-size:11px;background:#222;color:#ddd;border:1px solid #444;margin-top:4px}
 button{background:#2d5cd1;color:#fff;border:0;border-radius:4px;padding:5px 12px;cursor:pointer;font-size:13px}
 button:hover{background:#3d6ce1}
</style></head><body>
<h1>33.md 狐狸参考格式校准（F001-F015）</h1>
<div class="hint">点击画面循环：未评 → ✅可用 → ⭐首选 → ❌不可用；每格可填备注。<span id="stat"></span><br>
重点检查：狐狸身份一致性 / 白底·灰底泄漏 / 四足解剖 / 跳跃顶点是否碰到葡萄 / 姿态锁死 / 硬失败（双狐·多肢·变形·消失）</div>
<button onclick="playAll(true)">▶ 全部同步播放</button> <button onclick="playAll(false)">⏸ 全部暂停</button>

<h2>一、Reference Format 矩阵（同 Shot 配对 seed）</h2>
<table id="mx"></table>

<h2>二、Prompt Density 对比（Shot C × R3 winner，同 seed）</h2>
<table id="dn"></table>
<script>
const DATA = __DATA__;
const SHOT_T = __SHOTS__, REF_T = __REFS__, DENS = __DENS__;
let fb = {};
try { fb = JSON.parse(localStorage.getItem("f33_fb") || "{}"); } catch(e) {}
const CYCLE = ["", "ok", "best", "bad"];
const ICON = {"": "·", ok: "✅", best: "⭐", bad: "❌"};
const byId = {}; DATA.forEach(d=>byId[d.exp_id]=d);

function cellHTML(eid){
  const c = byId[eid]; if (!c) return "<td>—</td>";
  const st = (fb[eid]&&fb[eid].state) || "";
  return `<td><div class="cell" onclick="cycle('${eid}')">
    <video id="v_${eid}" src="${c.mp4}" loop muted controls preload="metadata"></video>
    <div class="lbl">${eid} · ${c.style} · motion=${c.motion ?? "?"}</div>
    <div class="state" id="s_${eid}">${ICON[st]}</div>
    <textarea class="note" id="n_${eid}" rows="2" placeholder="备注"
      onchange="saveNote('${eid}',this.value)">${(fb[eid]&&fb[eid].note)||""}</textarea>
  </div></td>`;
}
function render(){
  let h = "<tr><th></th>" + Object.keys(REF_T).map(r=>`<th>${r}<br><span style='font-weight:normal;color:#999'>${REF_T[r]}</span></th>`).join("") + "</tr>";
  for (const s of Object.keys(SHOT_T)){
    h += `<tr><td class="shot"><b>${s}</b><br>${SHOT_T[s]}<br><br><button onclick="playRow('${s}',true)">▶ 同步</button> <button onclick="playRow('${s}',false)">⏸</button></td>`;
    for (const r of Object.keys(REF_T)){
      const c = DATA.find(d=>d.shot===s && d.ref===r);
      h += c ? cellHTML(c.exp_id) : "<td>—</td>";
    }
    h += "</tr>";
  }
  document.getElementById("mx").innerHTML = h;
  let h2 = "<tr>";
  for (const [eid, lbl] of DENS){
    h2 += `<th>${eid}<br><span style='font-weight:normal;color:#999'>${lbl}</span></th>`;
  }
  h2 += "</tr><tr>";
  for (const [eid] of DENS){ h2 += cellHTML(eid); }
  h2 += "</tr>";
  document.getElementById("dn").innerHTML = h2;
  document.getElementById("stat").textContent =
    `（已标 ${Object.values(fb).filter(x=>x.state).length}/${DATA.length}）`;
}
function cycle(k){
  const cur = (fb[k]&&fb[k].state) || "";
  const next = CYCLE[(CYCLE.indexOf(cur)+1) % CYCLE.length];
  fb[k] = Object.assign({}, fb[k], {state: next});
  document.getElementById("s_"+k).textContent = ICON[next];
  persist(); render();
}
function saveNote(k, v){ fb[k] = Object.assign({}, fb[k], {note: v}); persist(); }
function persist(){
  localStorage.setItem("f33_fb", JSON.stringify(fb));
  fetch("/api/feedback", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({fb})}).catch(()=>{});
}
function playRow(s, on){
  for (const r of Object.keys(REF_T)){
    const c = DATA.find(d=>d.shot===s && d.ref===r);
    if (!c) continue;
    const v = document.getElementById("v_" + c.exp_id);
    if (v){ if (on){ v.currentTime = 0; v.play(); } else v.pause(); }
  }
}
function playAll(on){
  document.querySelectorAll("video").forEach(v=>{
    if (on){ v.currentTime = 0; v.play(); } else v.pause();
  });
}
fetch("/api/feedback").then(r=>r.json()).then(j=>{
  if (j && j.fb){ fb = Object.assign(fb, j.fb); }
  render();
}).catch(()=>{ render(); });
</script></body></html>"""
    html = html.replace("__DATA__", data_js)
    html = html.replace("__SHOTS__", json.dumps(SHOTS, ensure_ascii=False))
    html = html.replace("__REFS__", json.dumps(REFS, ensure_ascii=False))
    html = html.replace("__DENS__", json.dumps(DENSITY, ensure_ascii=False))
    out = E33 / "GALLERY.html"
    out.write_text(html, encoding="utf-8")
    print(f"GALLERY built: {out} ({len(data)} videos)")


if __name__ == "__main__":
    build()
