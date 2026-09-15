# _native_av_b1_review_server.py — Native AV B1 人工评审网站（scratch 工具）。
#
# 用途：评审 B1 E2E（12 条 A/B 视频，17.md §18）。每个样本展示：
#   Shot ID / A、B 标识 / 视频 / dialogue truth / expected speaker sequence；
#   用户对每条视频判 5 项：Dialogue correctness / Speaker assignment /
#   Language / Native lip-sync / Overall AV + 自由短评。
#
# 反馈落盘 _b1_dialogue_probe/results/B1_REVIEW_FEEDBACK.json。
# 🔴 安全：服务根目录=_b1_dialogue_probe/（不含 .env）；无目录穿越面
#   （所有视频路径来自 evidence/review_manifest.json，由 run_e2e.py 生成）。
#
# 启动："D:/Delta Force/python.exe" _native_av_b1_review_server.py → http://localhost:8910/
import json
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path("_b1_dialogue_probe").resolve()
MANIFEST = ROOT / "evidence" / "review_manifest.json"
FEEDBACK = ROOT / "results" / "B1_REVIEW_FEEDBACK.json"
PORT = 8910

DIMS = [
    {"key": "dialogue", "label": "① 台词正确性",
     "options": ["正确", "轻微问题", "错误"]},
    {"key": "speaker", "label": "② 说话人分配",
     "options": ["正确", "swap / 错人"]},
    {"key": "language", "label": "③ 语言",
     "options": ["正确", "错误"]},
    {"key": "lipsync", "label": "④ 原生 lip-sync",
     "options": ["可用", "不可用"]},
    {"key": "overall", "label": "⑤ 整体 AV",
     "options": ["PASS", "ACCEPTABLE", "FAIL"]},
]


def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def page_html():
    data = manifest()
    data["dims"] = DIMS
    return PAGE.replace("__MANIFEST__", json.dumps(data, ensure_ascii=False))


PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Native AV B1 评审 — Dialogue + Speaker Control</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#141619;color:#e8eaed}
 .wrap{max-width:1060px;margin:0 auto;padding:24px 16px 90px}
 h1{font-size:21px} h2{font-size:17px;margin:30px 0 8px;border-left:4px solid #bb9af7;padding-left:10px}
 h3{font-size:14px;margin:18px 0 4px;color:#c0caf5}
 .hint{color:#9aa0a6;font-size:13px}
 .truth{background:#1f2335;border:1px solid #3b4261;border-radius:10px;padding:10px 14px;margin:8px 0;font-size:13px;color:#c0caf5;white-space:pre-wrap}
 .card{background:#1c1f24;border:1px solid #2a2f36;border-radius:12px;padding:14px 16px;margin:14px 0}
 .item{margin:16px 0;padding-top:12px;border-top:1px dashed #2a2f36}
 .label{font-size:13px;color:#c0caf5;margin-bottom:4px}
 .badge{display:inline-block;border-radius:10px;padding:1px 10px;font-size:12px;margin-right:8px;font-weight:600}
 .badge.A{background:#33467e;color:#9ece6a}
 .badge.B{background:#5a3a7e;color:#e0af68}
 video{width:100%;max-height:320px;border-radius:8px;background:#000;margin:4px 0 8px}
 img{max-width:100%;max-height:160px;border-radius:8px;margin:4px 0 8px}
 .dims{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0;align-items:center}
 .dimrow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:4px 0}
 .dimname{font-size:12px;color:#9aa0a6;min-width:96px}
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
</style></head><body><div class="wrap">
<h1>Native AV B1 · Dialogue + Speaker Control 人工评审</h1>
<div class="note2">每个样本同镜头 / 同首帧 / 同模型(agnes-video-v2.0) / 同时长，<b>唯一差异 = prompt</b>：
<span class="tag">A</span>=当前生产方式（台词不进 prompt）；<span class="tag">B</span>=B1 台词+说话人控制（台词与 speaker 编译进 prompt）。
逐条视频判 5 项（点了即存），可加自由短评。评完告诉 Agent「评完了」。</div>
<div id="cards"></div>
</div>
<script>
const M = __MANIFEST__;
const DIMS = M.dims;
function post(entry){
  entry.ts = new Date().toLocaleString();
  fetch('/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(entry)}).then(r=>r.json()).then(j=>{
      if(j.ok){ const it=document.querySelector(`[data-spec="${entry.spec_id}"]`);
        if(it) it.classList.add('ok'); }});
}
M.samples.forEach(sm=>{
  const c=document.createElement('div'); c.className='card';
  const h=document.createElement('h2');
  h.textContent=`样本 ${sm.key} — ${sm.title}（${sm.shot_id}）`; c.appendChild(h);
  const g=document.createElement('div'); g.className='hint';
  g.textContent='考察点：'+sm.goal; c.appendChild(g);
  // dialogue truth + expected speaker sequence
  const t=document.createElement('div'); t.className='truth';
  let seq='';
  if(sm.expected_speaker_sequence){
    const em=sm.expected_speaker_sequence;
    seq = '预期 speaker 序列（'+em.resolution+'）：'+
      em.lines.map(l=>`${l.speaker}「${l.text}」`).join(' → ');
    if(em.silent && em.silent.length) seq += `；沉默：${em.silent.join('、')}`;
  } else { seq='（无台词）'; }
  t.textContent='台词 truth：'+sm.dialogue_truth+'\n'+seq;
  c.appendChild(t);
  if(sm.first_frame){ const im=document.createElement('img'); im.loading='lazy';
    im.src=sm.first_frame; im.alt='first frame'; c.appendChild(im); }
  // items sorted A first then B
  sm.items.forEach(it=>{
    const item=document.createElement('div'); item.className='item';
    item.dataset.spec=it.spec_id;
    const l=document.createElement('div'); l.className='label';
    l.innerHTML=`<span class="badge ${it.side}">${it.side}</span>`+
      `v${it.variant} <code>${it.spec_id}</code> — ${it.side_label}`;
    item.appendChild(l);
    const v=document.createElement('video'); v.controls=true; v.preload='none';
    v.src='/'+it.video; item.appendChild(v);
    // dims
    DIMS.forEach(d=>{
      const row=document.createElement('div'); row.className='dimrow';
      const nm=document.createElement('span'); nm.className='dimname';
      nm.textContent=d.label; row.appendChild(nm);
      d.options.forEach(op=>{
        const b=document.createElement('button');
        if(op.includes('错误')||op.includes('swap')||op.includes('不可用')||op==='FAIL')
          b.classList.add('warn');
        b.textContent=op;
        b.onclick=()=>{ [...row.querySelectorAll('button')].forEach(x=>x.classList.remove('on'));
          b.classList.add('on'); post({kind:'dims',spec_id:it.spec_id,sample:sm.key,
            side:it.side,variant:it.variant,dim:d.key,value:op}); };
        row.appendChild(b);
      });
      item.appendChild(row);
    });
    const i=document.createElement('input'); i.type='text';
    i.placeholder='本条短评（回车提交）…';
    i.onkeydown=e=>{ if(e.key==='Enter'&&i.value.trim()){
      post({kind:'note',spec_id:it.spec_id,sample:sm.key,side:it.side,note:i.value.trim()});
      i.value=''; } };
    item.appendChild(i);
    const sv=document.createElement('span'); sv.className='saved'; sv.textContent='✓ 已保存';
    item.appendChild(sv);
    // prompt details
    const det=document.createElement('details');
    det.innerHTML=`<summary>compiled prompt（${it.side} 侧）</summary>`;
    const pre=document.createElement('pre'); pre.textContent=it.prompt_preview;
    det.appendChild(pre); item.appendChild(det);
    c.appendChild(item);
  });
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
        print(f"manifest 不存在：{MANIFEST}\n先运行 _b1_dialogue_probe/run_e2e.py")
        raise SystemExit(1)
    print(f"review feedback -> {FEEDBACK}")
    print(f"http://localhost:{PORT}/   （Ctrl+C 停止）")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
