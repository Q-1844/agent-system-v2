# -*- coding: utf-8 -*-
"""Phase 45.4 — 评审页生成：Agent 自主生产版 vs 44C 执行侧推荐版 A/B。

页面内容：候选成片清单（明确标注「候选待审」还是「已正式定版」）、
逐镜时间线、逐句旁白表（可试听）、三段音乐试听、deep audit 音频证据、
渲染版本史、提案入口。媒体请求由 _44b3_review_server.py（带 Range）服务。
用法：python _4530_review_page.py
"""
import json
import html
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"
OUT = BASE / "_45" / "review"


def esc(s):
    return html.escape(str(s), quote=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    from src.sequence.service import SequenceService
    from src.audio.narration import NarrationStore
    pdir = BASE / "projects" / PROJECT
    svc = SequenceService(pdir, PROJECT)
    store = NarrationStore(pdir)
    cues = store.load()

    # 音频证据：对每个候选的最新 done 渲染跑一次逐层审计（真 ffmpeg，各 ~11s）
    from src.sequence.audio_audit import audit_audio_render

    def latest_done(entry):
        for r in reversed(entry.render_versions):
            if r.status == "done" and r.output_file and Path(r.output_file).exists():
                return r
        return None

    def audio_evidence(sid):
        try:
            plan = svc.get_audio_plan(sid)
            entry = svc.get_entry(sid)
            r = latest_done(entry)
            if plan is None or not plan.layers or r is None:
                return None
            out = audit_audio_render(
                mix_file=str(r.output_file),
                layers=[{"layer_id": l.layer_id, "role": l.role,
                         "source_file": l.source.file or "",
                         "start_ms": l.start_ms, "duration_ms": l.duration_ms,
                         "source_in_ms": l.source_in_ms, "gain_db": l.gain_db,
                         "fade_in_ms": l.fade_in_ms, "fade_out_ms": l.fade_out_ms}
                        for l in plan.layers],
                project_output_dir=pdir / "output",
                base_gain_db=plan.base_gain_db,
                render_version_id=r.version_id)
            (OUT / f"audio_audit_{sid}_{r.version_id}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            return out
        except Exception as e:   # noqa: BLE001
            return {"error": str(e)[:200], "passed": False}

    candidates = [
        ("seq_b746d7f3", "Agent 自主生产版（Phase 45.1 新会话：自建 sequence/文案/TTS/plan）"),
        ("seq_a3d5b19b", "44C 执行侧推荐版（脚本驱动的工具验证样本，用户反馈'还不错'）"),
    ]
    # 45.4 委派运行若产生新 sequence，一并纳入
    from src.agents.tasks import AgentTaskStore
    tstore = AgentTaskStore(pdir, PROJECT)
    for t in tstore.list():
        sr = (t.structured_result or {}).get("assembly_evidence") or {}
        for sid in sr.get("sequence_ids") or []:
            if sid not in [c[0] for c in candidates]:
                candidates.insert(0, (sid, "45.4 Producer 委派轮产出"))

    sections = []
    for sid, label in candidates:
        try:
            entry = svc.get_entry(sid)
        except Exception:
            continue
        block = entry.block
        plan = svc.get_audio_plan(sid)
        fr = entry.final_render
        fr_status = "已正式定版" if fr else "候选待审（select final_render 待用户执行）"
        done = latest_done(entry)
        audit = audio_evidence(sid) if done else None
        rver = done.version_id if done else "—"

        # 逐镜时间线
        rows = []
        cursor = 0
        for i, c in enumerate(block.clips):
            dur = 8000  # 快照实测已在 manifest；此处仅展示用近似（表头注明）
            rows.append(f"<tr><td>{i+1}</td><td>{esc(c.shot_id)}</td>"
                        f"<td>{cursor}</td><td>{cursor + (c.trim_out_ms or 0)}</td></tr>")
            cursor += dur
        timeline = ("<table class='t'><tr><th>#</th><th>shot</th><th>起(ms)</th>"
                    "<th>止(ms)</th></tr>" + "".join(rows) + "</table>")

        # 逐句旁白（来自 plan narration 层 + 旁白仓）
        narr_rows = []
        if plan:
            for l in plan.layers:
                if l.role != "narration":
                    continue
                info = ""
                vfile = ""
                for cid, e in cues.items():
                    for v in (e.get("versions") or []):
                        if v.get("audio_import_path") == l.source.file:
                            info = f"{esc(v['text'])}"
                            vfile = v.get("audio_import_path") or ""
                if vfile:
                    vfile = f"<audio controls preload='none' src='/media/projects/{PROJECT}/output/{esc(vfile)}'></audio>"
                narr_rows.append(f"<tr><td>{esc(l.layer_id)}</td>"
                                 f"<td>{l.start_ms}–{l.start_ms + l.duration_ms}</td>"
                                 f"<td>{info}</td><td>{vfile}</td></tr>")
        narr_table = ("<table class='t'><tr><th>层</th><th>时间窗(ms)</th><th>文案</th>"
                      "<th>试听</th></tr>" + "".join(narr_rows) + "</table>"
                      if narr_rows else "<p>（无 plan 或无旁白层）</p>")

        # 音乐层
        mus_rows = []
        if plan:
            for l in plan.layers:
                if l.role != "music":
                    continue
                src = f"<audio controls preload='none' src='/media/projects/{PROJECT}/output/{esc(l.source.file)}'></audio>"
                mus_rows.append(f"<tr><td>{esc(l.layer_id)}</td><td>{l.start_ms}–{l.start_ms + l.duration_ms}</td>"
                                f"<td>{l.gain_db} dB</td><td>{esc(l.source.file)}</td><td>{src}</td></tr>")
        mus_table = ("<table class='t'><tr><th>层</th><th>时间窗(ms)</th><th>增益</th>"
                     "<th>素材</th><th>素材试听</th></tr>" + "".join(mus_rows) + "</table>"
                     if mus_rows else "<p>（无音乐层）</p>")

        # 审计摘要
        audit_html = "<p>（无音频证据）</p>"
        if audit:
            if audit.get("error"):
                audit_html = f"<p class='bad'>审计失败: {esc(audit['error'])}</p>"
            else:
                n_pass = sum(1 for r in audit["narration_layers"] if r["pass"])
                m_pass = sum(1 for r in audit["music_layers"] if r["pass"])
                ld = audit.get("loudness") or {}
                audit_html = f"""
<ul>
<li>整体：{'<span class=ok>PASSED</span>' if audit.get('passed') else '<span class=bad>FAILED</span>'}
（旁白 {n_pass}/{len(audit['narration_layers'])}，音乐 {m_pass}/{len(audit['music_layers'])}，原生泄漏 {'pass' if (audit.get('native_leakage') or {}).get('pass') else 'FAIL'}）</li>
<li>响度：I={esc(ld.get('integrated_lufs'))} LUFS，TruePeak={esc(ld.get('true_peak'))} {esc(ld.get('true_peak_unit') or '')}，LRA={esc(ld.get('lra_lu'))} LU</li>
<li>逐句明细：<code>_45/review/audio_audit_{esc(sid)}_{esc(rver)}.json</code></li>
</ul>"""

        # 渲染版本史
        rv_rows = "".join(
            f"<tr><td>{esc(r.version_id)}</td><td>{esc(r.status)}</td>"
            f"<td>{esc(r.duration_ms or '')}</td><td>{esc((r.error or '')[:80])}</td></tr>"
            for r in entry.render_versions)
        rv_table = ("<table class='t'><tr><th>版本</th><th>状态</th><th>时长(ms)</th>"
                    "<th>错误</th></tr>" + rv_rows + "</table>")

        audio_tag = ""
        if done:
            audio_tag = (f"<audio controls preload='none' style='width:640px' "
                         f"src='/media/projects/{PROJECT}/output/sequences/{esc(sid)}/{esc(done.version_id)}.mp4'></audio>")

        sections.append(f"""
<section>
<h2>{esc(sid)} — {esc(label)}</h2>
<p><b>状态：{esc(fr_status)}</b>｜最新 done 渲染：<code>{esc(rver)}</code>｜
clips={len(block.clips)}｜plan={'rev ' + esc(plan.revision) if plan else '无'}</p>
<h3>成片试听（音视频）</h3>
{audio_tag or '<p>无 done 渲染</p>'}
<h3>Deep Audit 音频证据（逐句旁白 / 逐段音乐 / 原生泄漏 / 响度）</h3>
{audit_html}
<h3>逐句旁白</h3>
{narr_table}
<h3>分场景音乐</h3>
{mus_table}
<h3>渲染版本史（append-only，旧版可回看）</h3>
{rv_table}
<h3>时间线（快照顺序，整镜硬切）</h3>
{timeline}
</section>""")

    page = f"""<!doctype html><html lang=zh><meta charset=utf-8>
<title>Phase 45 成片评审</title>
<style>
body{{font-family:system-ui,'Microsoft YaHei';margin:24px;max-width:1080px;color:#222}}
h1{{font-size:22px}} h2{{font-size:18px;border-bottom:2px solid #eee;padding-bottom:6px}}
h3{{font-size:15px;margin-top:18px}}
table.t{{border-collapse:collapse;font-size:13px;margin:8px 0}}
table.t td,table.t th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}
.ok{{color:#0a7d32;font-weight:700}} .bad{{color:#b3261e;font-weight:700}}
code{{background:#f4f4f4;padding:1px 4px}}
.note{{background:#fff8e1;border:1px solid #f0e0a0;padding:10px 14px;border-radius:8px}}
</style>
<h1>《雨灯归航》成片评审（Phase 45）</h1>
<div class=note>
<b>定版状态说明</b>：本项目所有 render 均为<b>候选</b>；正式定版需用户显式执行
<code>select final_render</code>（REST /renders/select，Agent 无此工具）。当前
final_render 全部为空 = 全部待审。旧版可回看（append-only，零覆盖）。
</div>
{''.join(sections)}
<p style='color:#888;margin-top:32px'>生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')}｜
服务：python _44b3_review_server.py --root .. --port 8787 （媒体带 HTTP Range，可拖动进度条）</p>
</html>"""
    (OUT / "REVIEW_45.html").write_text(page, encoding="utf-8")
    print(f"written: {OUT / 'REVIEW_45.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
