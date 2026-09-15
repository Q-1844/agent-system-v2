# _native_av_c1_review_server.py — Native AV C1 人工评审网站（scratch 工具）。
#
# 用途：评审 C1 Production Foundation E2E（8 条生产路径视频，18.md §33-§35）。
# 每个样本展示：路由（UNBOUND/BOUND）/ 视频 / dialogue truth / 预期 speaker 序列
# （含舞台指示与语气提示）/ generation_basis / freshness。
# 用户对每条视频判 10 项（§34）：Dialogue correctness / Speaker correctness /
# Language / Lip-sync / Natural performance / Visual quality / SFX ambience fit /
# Unexpected BGM / Overall AV +（BOUND 样本）visual identity continuity + 短评。
#
# 反馈落盘 _c1_native_av_e2e/results/C1_REVIEW_FEEDBACK.json。
# 🔴 安全：服务根目录=_c1_native_av_e2e/（不含 .env）；无目录穿越面
#   （所有视频路径来自 evidence/review_manifest.json，由 run_e2e.py 生成）。
#
# 启动："D:/Delta Force/python.exe" _native_av_c1_review_server.py → http://localhost:8911/
import json
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path("_c1_native_av_e2e").resolve()
MANIFEST = ROOT / "evidence" / "review_manifest.json"
FEEDBACK = ROOT / "results" / "C1_REVIEW_FEEDBACK.json"
PORT = 8911

DIMS = [
    {"key": "dialogue", "label": "① 台词正确性",
     "options": ["正确", "轻微问题", "错误"]},
    {"key": "speaker", "label": "② 说话人分配",
     "options": ["正确", "swap / 错人"]},
    {"key": "language", "label": "③ 语言",
     "options": ["正确", "错误"]},
    {"key": "lipsync", "label": "④ 原生 lip-sync",
     "options": ["可用", "不可用"]},
    {"key": "natural", "label": "⑤ 表演自然度",
     "options": ["自然", "一般", "僵硬/机械"]},
    {"key": "visual", "label": "⑥ 视觉质量",
     "options": ["好", "一般", "差"]},
    {"key": "sfx", "label": "⑦ SFX/环境声贴合",
     "options": ["贴合", "无明显声音", "不贴合"]},
    {"key": "bgm", "label": "⑧ 意外 BGM",
     "options": ["无 BGM（符合预期）", "有轻微 BGM", "有明显 BGM"]},
    {"key": "continuity", "label": "⑨ 视觉连续性（仅 G/BOUND）",
     "options": ["不适用", "保持", "退化"]},
    {"key": "overall", "label": "⑩ 整体 AV",
     "options": ["PASS", "ACCEPTABLE", "FAIL"]},
]


def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def page_html():
    data = manifest()
    data["dims"] = DIMS
    return PAGE.replace("__MANIFEST__", json.dumps(data, ensure_ascii=False))


PAGE = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Native AV C1 评审 — Production Foundation</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#141619;color:#e8eaed}
 .wrap{max-width:1060px;margin:0 auto;padding:24px 16px 90px}
 h1{font-size:21px} h2{font-size:17px;margin:30px 0 8px;border-left:4px solid #bb9af7;padding-left:10px}
 .hint{color:#9aa0a6;font-size:13px}
 .truth{background:#1f2335;border:1px solid #3b4261;border-radius:10px;padding:10px 14px;margin:8px 0;font-size:13px;color:#c0caf5;white-space:pre-wrap}
 .card{background:#1c1f24;border:1px solid #2a2f36;border-radius:12px;padding:14px 16px;margin:14px 0}
 .label{font-size:13px;color:#c0caf5;margin-bottom:4px}
 .badge{display:inline-block;border-radius:10px;padding:1px 10px;font-size:12px;margin-right:8px;font-weight:600}
 .badge.UNBOUND{background:#2a4a3a;color:#9ece6a}
 .badge.BOUND{background:#5a3a7e;color:#e0af68}
 video{width:100%;max-height:340px;border-radius:8px;background:#000;margin:4px 0 8px}
 .dims{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0;align-items:center}
 .dimrow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:4px 0}
 .dimname{font-size:12px;color:#9aa0a6;min-width:120px}
 button{border:1px solid #3a4048;background:#23272e;color:#e8eaed;border-radius:14px;
        padding:4px 11px;font-size:12px;cursor:pointer}
 button:hover{background:#2c313a}
 button.on{background:#bb9af7;border-color:#bb9af7;color:#141619;font-weight:600}
 button.on.warn{background:#e0af68;border-color:#e0af68}
 input[type=text]{width:100%;box-sizing:border-box;background:#23272e;border:1px solid #3a4048;
        color:#e8eaed;border-radius:8px;padding:7px 10px;font-size:13px}
 details{margin:6px 0} summary{cursor:pointer;font-size:12px;color:#9aa0a6}
 pre{font-size:11px;color:#7982a0;white-space:pre-wrap;word-break:break-all}
 .saved{color:#9ece6a;font-size:12px;margin-left:8px;display:none}
 .ok .saved{display:inline}
 .note2{background:#1f2335;border:1px solid #3b4261;border-radius:10px;padding:10px 14px;margin:10px 0;font-size:13px;color:#c0caf5}
 .tag{color:#bb9af7}
 .fr{font-size:12px;color:#7aa2f7}
</style></head><body><div class="wrap">
<h1>Native AV C1 · Production Foundation 人工评审</h1>
<div class="note2">8 个样本全部走<b>生产 handler 全链</b>（含 VideoVersion / generation_basis /
freshness / final_video）：<span class="tag">UNBOUND</span>=legacy 视觉路径 + AV 编译区；
<span class="tag">BOUND</span>=完整视觉生产 binding + boundary frames + 同一 AV 编译区。
重点判断（§35）：① productionization 是否引入 regression ② 新 Prompt Compiler 是否减少误读/BGM
③ Audio Intent 是否改善动作-声音匹配 ④ BOUND 路径是否同样稳定 ⑤ freshness 是否正确。
逐条视频判 10 项（点了即存），可加自由短评。评完告诉 Agent「评完了」。</div>
<div id="cards"></div>
</div>
<script>
const M = __MANIFEST__;
const DIMS = M.dims;
function post(entry){
  entry.ts = new Date().toLocaleString();
  fetch('/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(entry)}).then(r=>r.json()).then(j=>{
      if(j.ok){ const it=document.querySelector(`[data-key="${entry.sample}"]`);
        if(it) it.classList.add('ok'); }});
}
M.samples.forEach(sm=>{
  const c=document.createElement('div'); c.className='card';
  c.dataset.key=sm.key;
  const h=document.createElement('h2');
  h.textContent=`样本 ${sm.key}（${sm.route.toUpperCase()} · ${sm.shot_id}）`; c.appendChild(h);
  const g=document.createElement('div'); g.className='hint';
  g.textContent='出场角色：'+(sm.characters||[]).join('、')+'　项目：'+sm.project; c.appendChild(g);
  // dialogue truth + expected speaker sequence
  const t=document.createElement('div'); t.className='truth';
  let seq='';
  const em=sm.expected_speaker_sequence;
  if(em && em.lines){
    seq = '预期 speaker 序列（'+em.resolution+'）：'+
      em.lines.map(l=>`${l.speaker}「${l.text}」`+
        (l.delivery_hints&&l.delivery_hints.length?`（提示：${l.delivery_hints.join('/')}，不朗读）`:''))
        .join(' → ');
    if(em.directions && em.directions.length)
      seq += '\n预期舞台指示（不朗读）：'+em.directions.map(d=>`「${d.text}」`).join('；');
    if(em.silent && em.silent.length) seq += `\n沉默角色：${em.silent.join('、')}`;
  } else if(em && em.resolution==='no_dialogue'){ seq='（无台词）'; }
  else if(em && em.error){ seq='（解析异常：'+em.error+'）'; }
  t.textContent='台词 truth：'+sm.dialogue_truth+'\n'+seq; c.appendChild(t);
  const fr=document.createElement('div'); fr.className='fr';
  fr.textContent='freshness: '+(sm.freshness? sm.freshness.status+' '+JSON.stringify(sm.freshness.detail||{}) : 'n/a');
  c.appendChild(fr);
  if(sm.video){
    const v=document.createElement('video'); v.controls=true; v.preload='none';
    v.src='/'+sm.video; c.appendChild(v);
  } else {
    const e=document.createElement('div'); e.className='truth';
    e.textContent='生成失败：'+(sm.error||'unknown'); c.appendChild(e);
  }
  DIMS.forEach(d=>{
    if(d.key==='continuity' && sm.route!=='bound') return;   // 连续性仅 BOUND
    const row=document.createElement('div'); row.className='dimrow';
    const nm=document.createElement('span'); nm.className='dimname';
    nm.textContent=d.label; row.appendChild(nm);
    d.options.forEach(op=>{
      if(d.key==='continuity' && op==='不适用') return;
      const b=document.createElement('button');
      if(op.includes('错误')||op.includes('swap')||op.includes('不可用')||op==='FAIL'||
         op.includes('僵硬')||op.includes('明显 BGM')||op.includes('退化')||op.includes('不贴合')||op==='差')
        b.classList.add('warn');
      b.textContent=op;
      b.onclick=()=>{ [...row.querySelectorAll('button')].forEach(x=>x.classList.remove('on'));
        b.classList.add('on'); post({kind:'dims',sample:sm.key,route:sm.route,
          shot_id:sm.shot_id,dim:d.key,value:op}); };
      row.appendChild(b);
    });
    c.appendChild(row);
  });
  const i=document.createElement('input'); i.type='text';
  i.placeholder='本条短评（回车提交）…';
  i.onkeydown=e=>{ if(e.key==='Enter'&&i.value.trim()){
    post({kind:'note',sample:sm.key,route:sm.route,shot_id:sm.shot_id,note:i.value.trim()});
    i.value=''; } };
  c.appendChild(i);
  const sv=document.createElement('span'); sv.className='saved'; sv.textContent='✓ 已保存';
  c.appendChild(sv);
  // prompt + basis details
  const det=document.createElement('details');
  det.innerHTML='<summary>compiled prompt / generation_basis / 证据</summary>';
  let txt='';
  if(sm.prompt_snapshot_full) txt+='compiled prompt:\n'+sm.prompt_snapshot_full+'\n\n';
  if(sm.generation_basis) txt+='generation_basis:\n'+JSON.stringify(sm.generation_basis,null,2)+'\n\n';
  txt+='duration: '+(sm.duration_s||'?')+'s  size: '+(sm.size||'?');
  const pre=document.createElement('pre'); pre.textContent=txt;
  det.appendChild(pre); c.appendChild(det);
  document.getElementById('cards').appendChild(c);
});
</script></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = page_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/submit":
            n = int(self.headers.get("Content-Length", 0))
            entry = json.loads(self.rfile.read(n).decode("utf-8"))
            entry["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
            fb = {"entries": []}
            if FEEDBACK.exists():
                try:
                    fb = json.loads(FEEDBACK.read_text(encoding="utf-8"))
                except Exception:
                    fb = {"entries": []}
            fb.setdefault("entries", []).append(entry)
            FEEDBACK.write_text(json.dumps(fb, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if not MANIFEST.exists():
        print(f"manifest 不存在：{MANIFEST}\n先运行 _c1_native_av_e2e/run_e2e.py")
        raise SystemExit(1)
    print(f"review feedback -> {FEEDBACK}")
    print(f"http://localhost:{PORT}/   （Ctrl+C 停止）")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
