# -*- coding: utf-8 -*-
"""复验一轮+盲测+说明片 联合轮询。"""
import json, sys, time, urllib.request
WATCH = [
    ("P48_E_RV1", "run_736f96b6d1f1"),
    ("P48_E_Blind", "run_240a4e34419e"),
    ("P48_E_Explainer", "run_1f8898ce81b7"),
]
def get(path):
    with urllib.request.urlopen(f"http://localhost:8000{path}", timeout=60) as r:
        return json.loads(r.read() or b"{}")
t0=time.time()
while time.time()-t0 < int(sys.argv[1] if len(sys.argv)>1 else 7200):
    lines=[]; hit=False
    for p,rid in WATCH:
        try: d=get(f"/api/project/{p}/workflow/runs/{rid}")
        except Exception as e:
            lines.append(f"{p}:ERR"); continue
        st=d.get("status")
        pend=[(r.get('requestId'),r.get('nodeId')) for r in d.get('reviews') or [] if r.get('status')=='pending']
        failed={k:(v.get('error') or '')[:60] for k,v in (d.get('nodes') or {}).items() if v.get('status')=='failed'}
        ok=sum(1 for v in (d.get('nodes') or {}).values() if v.get('status')=='success')
        tag=f"{p}[{st}]ok={ok}"
        if pend: tag+=f" PEND={pend}"; hit=True
        if failed: tag+=f" FAIL={failed}"; hit=True
        lines.append(tag)
    print(f"[{time.strftime('%H:%M:%S')}] "+" || ".join(lines), flush=True)
    if hit:
        print('ATTENTION'); raise SystemExit(0)
    time.sleep(30)
