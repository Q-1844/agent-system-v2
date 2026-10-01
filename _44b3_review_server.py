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

    # ---- Phase 44C 区块 ----
    cdir = BASE_DIR / "_44c"
    C_PROJECT = "雨灯归航"
    cparts = []
    try:
        mix_ev = json.loads((cdir / "mix_evidence.json").read_text(encoding="utf-8"))
        cue_ev = json.loads((cdir / "cue_table.json").read_text(encoding="utf-8"))
    except Exception:
        mix_ev, cue_ev = {}, {}
    if cue_ev or mix_ev:
        ab = cue_ev.get("voice_ab") or {}
        ab_html = "".join(
            f"<div class='abitem'><b>{html.escape(s.get('label',''))}</b>"
            f"<audio controls preload='none' src='{html.escape(_rel(str(BASE_DIR / 'projects' / C_PROJECT / 'output' / s.get('file',''))))}'></audio>"
            f"<span class='note'>{html.escape(str(s.get('duration_s','?')))}s "
            f"{html.escape(s.get('voice_id',''))}</span></div>"
            for s in (ab.get("samples") or []) if s.get("file"))
        cparts.append(
            "<section><h2>C1/C2｜旁白声线 A/B（同句三声线试听）</h2>"
            + (ab_html or "<p class='warn'>无 A/B 样张</p>") + "</section>")
        cues = cue_ev.get("narration_cues") or []
        if cues:
            rows = "".join(
                f"<tr><td>{c['cue_id']}</td><td>{c['shot_id']}</td>"
                f"<td>{c['start_ms']}–{c['end_ms']}</td><td>{c['duration_ms']}ms</td>"
                f"<td>{html.escape(c['text'])}</td><td>{html.escape(c.get('why','')[:60])}</td>"
                f"<td>{'✓' if c.get('fits_window') else '✗'}</td></tr>" for c in cues)
            cparts.append(
                "<section><h2>C2｜旁白 cue 表（执行侧候选 nc01–05，落点已实测重排）</h2>"
                "<table><tr><th>cue</th><th>镜</th><th>时间线 ms</th><th>时长</th>"
                "<th>文字稿</th><th>依据</th><th>落窗</th></tr>" + rows + "</table></section>")
        m = mix_ev.get("mixed_render") or {}
        meas = mix_ev.get("measurements") or {}
        if m.get("output"):
            cparts.append(
                "<section><h2>C3｜混音成片（执行侧 cue 版，旁白+音乐+ducking+loudnorm）</h2>"
                + video_block("全片混音版（native 环境声 -3dB + 音乐 -10dB + 旁白 +2dB）",
                              m["output"],
                              f"render {m.get('render_version_id')} / plan rev "
                              f"{(mix_ev.get('audio_plan') or {}).get('revision')} / "
                              f"渲染 {m.get('seconds')}s")
                + f"<p>测量：I={meas.get('final',{}).get('i_lufs')} LUFS（目标 -16），"
                  f"峰值={meas.get('final',{}).get('peak_dbfs')} dBFS，"
                  f"A/V 时长差={meas.get('av_duration',{}).get('delta_ms')}ms</p></section>")
        sb = mix_ev.get("silent_baseline") or {}
        if sb.get("output"):
            cparts.append(
                "<section><h2>C3｜静音画面基线（全片，audio=silent）</h2>"
                + video_block("静音基线", sb["output"],
                              f"render {sb.get('render_version_id')} / {sb.get('duration_ms')}ms")
                + "</section>")
        stems = mix_ev.get("stems") or {}
        if stems:
            stem_html = "".join(
                f"<div class='abitem'><b>{html.escape(k)}</b>"
                f"<audio controls preload='none' src='{html.escape(_rel(v['file']))}'></audio>"
                f"<span class='note'>plan rev {html.escape(v.get('plan_revision',''))}</span></div>"
                for k, v in stems.items())
            cparts.append(
                "<section><h2>C3｜分轨（可追溯：文件名含 plan revision）</h2>"
                + stem_html + "</section>")
        # Agent 版混音渲染（A/B 的 B 侧）
        try:
            ws = json.loads((cdir / "cagent_wait_state.json").read_text(encoding="utf-8"))
            sdoc = json.loads((BASE_DIR / "projects" / C_PROJECT / "output" /
                               "sequences.json").read_text(encoding="utf-8"))
            row = sdoc["sequences"][ws["sequence_id"]]["render_versions"]
            arow = next(r for r in row if r["version_id"] == ws["render_version_id"])
            if arow["status"] == "done":
                cparts.append(
                    "<section><h2>C 验收｜Agent 版混音成片（A/B 的 B 侧）</h2>"
                    + video_block(f"Agent cue 集（na01–na05）混音版",
                                  arow["output_file"],
                                  f"render {arow['version_id']} / "
                                  f"{arow.get('duration_ms')}ms / plan rev 见 manifest_v3")
                    + "</section>")
        except Exception:
            pass
        cagent = {}
        try:
            cagent = json.loads((cdir / "cagent_run_summary.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        if cagent:
            cparts.append(
                "<section><h2>C 验收｜Assembly Agent 真实声音会话</h2><pre>"
                + html.escape(json.dumps(
                    {k: v for k, v in cagent.items() if not k.endswith("receipts")},
                    ensure_ascii=False, indent=1))
                + "</pre><p>工具轨迹：_44c/events_cagent_1.jsonl / events_cagent_2.jsonl</p>"
                + "</section>")

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
<h1>Phase 44C｜旁白 · 音乐 · 混音（雨灯归航）</h1>
{''.join(cparts)}
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

    # ---- Phase 44C+ 修复：支持 HTTP Range（浏览器媒体进度条 seek 必需；
    # 无 Range 支持时音频只能从头播、拖动失效——用户实测"分轨只有一句"）----
    def send_head(self):
        path = self.translate_path(self.path)
        import os
        if not (os.path.isdir(path) or os.path.exists(path)):
            return super().send_head()
        rng = self.headers.get("Range")
        if not rng or not rng.startswith("bytes="):
            return super().send_head()
        try:
            size = os.path.getsize(path)
            unit, _, spec = rng.partition("=")
            start_s, _, end_s = spec.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
            ctype = self.guess_type(path)
            f = open(path, "rb")
            f.seek(start)
            n = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(n))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", self.date_time_string(os.path.getmtime(path)))
            self.end_headers()
            return _RangeFile(f, n)
        except Exception:
            return super().send_head()


class _RangeFile:
    """有限长度文件包装（Range 206 响应体）。"""

    def __init__(self, f, remaining):
        self.f = f
        self.remaining = remaining

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        n = self.remaining if size < 0 else min(size, self.remaining)
        data = self.f.read(n)
        self.remaining -= len(data)
        return data


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
