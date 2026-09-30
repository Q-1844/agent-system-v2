# -*- coding: utf-8 -*-
"""Phase 44B — 生成用户可评审的静态预览页 + 附带 HTTP 服务。

  python _44b3_review_server.py --build   # 仅生成 _44b/review/REVIEW_44b.html
  python _44b3_review_server.py           # 生成 + 起服务 http://127.0.0.1:8786/REVIEW_44b.html
"""
import argparse
import html
import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
REVIEW_DIR = BASE_DIR / "_44b" / "review"
PORT = 8786


def _rel(p: str) -> str:
    """仓库根相对路径 → review 页可用的相对 URL（review 页在 /_44b/review/ 下）。"""
    p = str(p).replace("\\", "/")
    if p.lower().startswith(str(BASE_DIR).replace("\\", "/").lower()):
        p = p[len(str(BASE_DIR).replace("\\", "/")) + 1:]
    depth = len(REVIEW_DIR.relative_to(BASE_DIR).parts)
    return "../" * depth + p


def build() -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    ev = json.loads((BASE_DIR / "_44b" / "e2e_evidence.json").read_text(encoding="utf-8"))
    agent_summary = {}
    agent_file = BASE_DIR / "_44b" / "agent_run_summary.json"
    if agent_file.exists():
        agent_summary = json.loads(agent_file.read_text(encoding="utf-8"))

    def video_block(title, path, note=""):
        if not path or not (BASE_DIR / path.replace("\\", "/")).exists():
            return f"<h3>{html.escape(title)}</h3><p class='warn'>文件缺失：{html.escape(str(path))}</p>"
        return (f"<h3>{html.escape(title)}</h3>"
                f"<video controls preload='metadata' src='{html.escape(_rel(path))}'></video>"
                f"<p class='note'>{html.escape(note)}</p>")

    def timeline_table(items):
        rows = "".join(
            f"<tr><td>{it['index']}</td><td>{it['shot_id']}</td>"
            f"<td>{it['scene_id']}{' ⚡跨组' if it['scene_boundary'] else ''}</td>"
            f"<td>{it['video_version_id']}</td>"
            f"<td>{it['approved_status']}</td>"
            f"<td>{it['source_in_out_ms'][0]}–{it['source_in_out_ms'][1]}</td>"
            f"<td>{it['timeline_start_end_ms'][0]}–{it['timeline_start_end_ms'][1]}</td></tr>"
            for it in items)
        return ("<table><tr><th>#</th><th>镜</th><th>场</th><th>来源版本</th>"
                "<th>裁定</th><th>源区间 ms</th><th>时间线 ms</th></tr>"
                + rows + "</table>")

    parts = []
    a = ev.get("part_a")
    if a:
        parts.append(
            "<section><h2>1｜3 镜小样本（B4.1：同组接力 + 跨组切换）</h2>"
            + video_block("小样本成片（native 原生环境声）", a["output"],
                          f"sequence {a['sequence_id']} / render {a['render_version_id']} / "
                          f"渲染 {a['render_seconds']}s / 实测 {a['probe']['duration_ms']}ms"
                          f"（计划 {a['seam_audit'] and ''}）")
            + f"<p>切点机器检查：{a['seam_audit']['seams_with_issues']} 处异常"
              f"（{len(a['seam_audit']['seams'])} 切点）；帧审计 issues："
              f"{html.escape(json.dumps(a['frame_audit_issues'], ensure_ascii=False))}</p>"
            + "<div class='frames'>" + "".join(
                f"<figure><img src='{html.escape(_rel(f['file']))}' loading='lazy'>"
                f"<figcaption>t={f['t_ms']}ms</figcaption></figure>"
                for f in a["previews"]) + "</div></section>")
    a2 = ev.get("part_a2_silent")
    if a2:
        parts.append(
            "<section><h2>1b｜静音策略渲染（B3：audio=silent 双策略）</h2>"
            + video_block("小样本成片（silent 整片静音轨）", a2["output"],
                          f"render {a2['render_version_id']} / 渲染 {a2['render_seconds']}s / "
                          f"静音段（>-60dB, >0.5s）：{a2['silence_segments_gt_0.5s']} —— "
                          f"输出含等长静音轨，原生声已关闭")
            + f"<p>freshness：native={a2['freshness_native_after_revert']}、"
              f"silent={a2['freshness_silent_after_revert']}（策略回退后的状态机）</p></section>")
    b = ev.get("part_b")
    if b:
        parts.append(
            "<section><h2>2｜全片 13 镜工程预览（B4.2）</h2>"
            + video_block("全片成片（89.9s，native）", b["output"],
                          f"sequence {b['sequence_id']} / render {b['render_version_id']} / "
                          f"渲染 {b['render_seconds']}s / 片长 {b['total_duration_ms']}ms / "
                          f"manifest_v{b['manifest_revision']}")
            + timeline_table(b["timeline_table"])
            + "<h3>接缝机器检查</h3><p>"
            + html.escape(json.dumps(
                {f"seam@{s['seam_ms']}ms": s["issues"] for s in b["seam_audit"]["seams"]
                 if s["issues"]}, ensure_ascii=False))
            + f"</p><p>12 切点中 {12 - b['seam_audit']['seams_with_issues']} 处全净；"
              f"帧级 issues：{html.escape(json.dumps(b['frame_audit_issues'], ensure_ascii=False))}</p>"
            + "<h3>快照期接缝 MAE（44A 实测，风险排序）</h3><p>"
            + html.escape(json.dumps(b.get("seam_mae_at_snapshot", []), ensure_ascii=False))
            + "</p></section>")
    c = ev.get("part_c_trim")
    if c:
        parts.append(
            "<section><h2>3｜修改测试（B4.3）</h2><pre>"
            + html.escape(json.dumps(c, ensure_ascii=False, indent=1))
            + "</pre></section>")
    cs = ev.get("part_c_swap")
    if cs:
        parts.append(
            "<section><h2>3b｜版本替换测试（B4.3：fixture）</h2><pre>"
            + html.escape(json.dumps(cs, ensure_ascii=False, indent=1))
            + "</pre></section>")
    d = ev.get("part_d_interrupt")
    if d:
        parts.append(
            "<section><h2>4｜中断恢复测试（B4.4）</h2><pre>"
            + html.escape(json.dumps(d, ensure_ascii=False, indent=1))
            + "</pre></section>")
    if agent_summary:
        # Agent 自主装配的成片（B4.5）：从其 sequence 读 done 渲染输出
        agent_video = ""
        agent_seq = None
        for r in (agent_summary.get("turn1_receipts") or []):
            if r.get("operation") == "assembly.sequence_create" and r.get("ok"):
                agent_seq = r.get("sequence_id")
        agent_out = None
        if agent_seq:
            try:
                sdoc = json.loads((BASE_DIR / "projects" / "雨灯归航" / "output" /
                                   "sequences.json").read_text(encoding="utf-8"))
                e = sdoc["sequences"][agent_seq]
                done = [r for r in e["render_versions"] if r["status"] == "done"]
                if done:
                    agent_out = done[-1]["output_file"]
            except Exception:
                pass
        if agent_out:
            agent_video = video_block(
                "Agent 装配的全片成片（seq_" if False else
                f"Agent 装配的全片成片（{agent_seq}）", agent_out,
                "由 Assembly Agent 真实会话自主创建/渲染（工具轨迹见 "
                "_44b/events_agent_*.jsonl）")
        parts.append(
            "<section><h2>5｜Assembly Agent 真实会话（B4.5）</h2>" + agent_video + "<pre>"
            + html.escape(json.dumps(
                {k: v for k, v in agent_summary.items()
                 if k not in ("turn1_receipts", "turn2_receipts")},
                ensure_ascii=False, indent=1))
            + "</pre><p>完整工具轨迹见 _44b/events_agent_1.jsonl / events_agent_2.jsonl、"
              "result_agent_1.json / result_agent_2.json、session_after_agent.json。</p></section>")

    page = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Phase 44B 基础成片 · 工程预览评审</title><style>
body{{font-family:system-ui,"Segoe UI",sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;background:#141414;color:#ddd}}
h1{{font-size:20px}} h2{{font-size:16px;border-bottom:1px solid #333;padding-bottom:6px;margin-top:32px}}
h3{{font-size:14px}} video{{width:100%;max-width:880px;background:#000;border-radius:6px}}
table{{border-collapse:collapse;font-size:12px;width:100%}} td,th{{border:1px solid #333;padding:4px 8px;text-align:left}}
pre{{background:#1d1d1d;padding:12px;border-radius:6px;font-size:12px;overflow:auto}}
.frames{{display:flex;flex-wrap:wrap;gap:10px}} figure{{margin:0;width:200px}} img{{width:200px;border-radius:4px}}
figcaption{{font-size:11px;color:#888}} .note{{color:#9ab;font-size:12px}} .warn{{color:#f80}}
section{{margin-bottom:12px}}
</style></head><body>
<h1>Phase 44B｜基础成片工程预览（雨灯归航 · snap_44a_formal_20261001_001753）</h1>
<p class="note">状态：<b>工程预览</b>——成片基于正式快照装配；final_render 选择与成片定版由用户完成。
音轨=原生环境声（audio=native），未加任何 BGM/旁白；s02_c03 原生近静音为用户已知选择（甲-2）。</p>
{''.join(parts)}
</body></html>"""
    out = REVIEW_DIR / "REVIEW_44b.html"
    out.write_text(page, encoding="utf-8")
    print(f"review page → {out}")
    return out


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BASE_DIR), **kw)

    def log_message(self, fmt, *args):   # 静默
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="只生成页面不启动服务")
    a = ap.parse_args()
    os.chdir(BASE_DIR)
    build()
    if a.build:
        return
    print(f"serving http://127.0.0.1:{PORT}/_44b/review/REVIEW_44b.html")
    HTTPServer(("127.0.0.1", PORT), _Handler).serve_forever()


if __name__ == "__main__":
    main()
