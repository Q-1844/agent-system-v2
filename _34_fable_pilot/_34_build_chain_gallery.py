# _34_fable_pilot/_34_build_chain_gallery.py — 尾帧接力多轮链对比 Gallery。
# 每镜三列：v1c_fallback（resolver bug 白跑批=无接力对照）/ v1c_relay1 / v1c_relay2
# + links diff 矩阵 + R3 身份锚图 + s01 链根尾帧。供用户逐镜人评（漂移数据扩充实验）。
import html
import io
import json
import sys
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
OUT = HERE / "FABLE_PILOT_V1C_CHAIN_COMPARE.html"

CHAIN = ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]
RUNS = [  # (语义标签, 展示名, jsonl round 名, 序数区间)
    ("v1c_fallback", "无接力对照（bug 白跑批）", "v1c", (0, 6)),
    ("v1c_relay1", "接力链 1（09-23 00:19）", "v1c", (6, 12)),
    ("v1c_relay2", "接力链 2（09-23 13:xx 本轮）", "v1c_r3", (0, 6)),
]
# 运行期目录名与语义标签的映射（目录统一为语义名后为空）
DIR_ALIAS = {}
RELAY_NOTE = {
    "s02_c01": "参考=s01 尾帧（空园土路）——狐狸从画外入画",
    "s03_c01": "参考=s02 尾帧（行走中）",
    "s04_c01": "参考=s03 尾帧（CU 状态）",
    "s05_c01": "参考=s04 尾帧（蓄力结束态）",
    "s06_c01": "参考=s05 尾帧（落地状态）",
    "s07_c01": "参考=s06 尾帧（坐地仰望态）",
}
DRIFT_WATCH = {
    "s02_c01": "入画：毛色/白肚/尾尖 vs 锚图",
    "s03_c01": "近景面部：眼距/吻部白斑/耳形",
    "s04_c01": "CU：面部细节漂移高发位",
    "s05_c01": "腾空：四肢/体型比例",
    "s06_c01": "落地：体型+毛色",
    "s07_c01": "坐地仰望：链尾=累计漂移终态",
}


def records():
    return [json.loads(l) for l in
            (HERE / "v1c_runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def r3_cover_url():
    """狐狸 R3 cover（look_2c6a01d45db5 / ref_bcfeac7ed0d9）→ 图片 URL。"""
    try:
        d = httpx.get(
            "http://127.0.0.1:8000/api/project/Fable_Pilot_Fox_and_Grapes_V1"
            "/resources/look_2c6a01d45db5", timeout=10).json()
        urls = []

        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
            elif isinstance(o, str) and "cos-platform-outputs" in o and o.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                urls.append(o)
        walk(d)
        if urls:
            return urls[0]
    except Exception:
        pass
    return ""


def main():
    recs = records()
    runs_data = {}
    for label, _name, rnd, (lo, hi) in RUNS:
        same = [r for r in recs if r.get("round") == rnd and r.get("job_status") == "done"][lo:hi]
        runs_data[label] = {r["shot_id"]: r for r in same}
    metrics = json.loads((HERE / "chain_metrics.json").read_text(encoding="utf-8")) \
        if (HERE / "chain_metrics.json").exists() else {}

    cover = r3_cover_url()
    root_tail = f"_frames/{DIR_ALIAS.get('v1c_relay2', 'v1c_relay2')}/_s01_root_tail.png"

    def vid(p):
        return f'<video src="{html.escape(p)}" controls preload="metadata"></video>' \
            if Path(HERE / p).exists() else '<div class="miss">缺档</div>'

    def img(p):
        return f'<img src="{html.escape(p)}">' if Path(HERE / p).exists() else ""

    # diff 矩阵
    matrix = ['<table class="mtx"><tr><th>衔接链接（本镜首帧 vs 上镜末帧）</th>' +
              "".join(f'<th>{html.escape(n)}</th>' for _l, n, _r, _s in RUNS) + "</tr>"]
    for shot in CHAIN:
        row = [f"<tr><td>{shot}<span class='note'> {html.escape(RELAY_NOTE[shot])}</span></td>"]
        for label, _n, _r, _s in RUNS:
            v = (metrics.get(label) or {}).get("links", {}).get(shot)
            cls = "good" if (v is not None and 0 <= v <= 12) else ("mid" if v is not None and v <= 30 else "bad")
            row.append(f"<td class='{cls}'>{v if v is not None else '—'}</td>")
        matrix.append("".join(row) + "</tr>")
    matrix.append("</table>")

    parts = [f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>V1C 尾帧接力 — 多轮链对比（漂移数据扩充）</title>
<style>body{{font-family:system-ui;background:#141414;color:#eee;max-width:1250px;margin:0 auto;padding:1.5rem}}
h1{{color:#ffd166}}h2{{color:#ffd166;margin-top:2rem}}.meta{{color:#aaa;font-size:.85rem}}
.cand{{background:#1e1e1e;border-radius:10px;padding:.9rem;margin:.8rem 0}}
video{{width:100%;max-width:390px;border-radius:8px;background:#000}}
img{{max-width:190px;border-radius:6px;border:1px solid #333;margin:2px}}
.cols{{display:flex;gap:1rem;flex-wrap:wrap}}.cols>div{{flex:1;min-width:330px}}
.mtx{{border-collapse:collapse;margin:.8rem 0}}.mtx td,.mtx th{{border:1px solid #333;padding:4px 10px;font-size:.9rem}}
td.good{{color:#7CFC98}}td.mid{{color:#ffd166}}td.bad{{color:#ff7b72}}.note{{color:#888;font-size:.78rem}}
.anchor{{display:flex;gap:1rem;align-items:flex-start;background:#1e1e1e;border-radius:10px;padding:.8rem}}
.pill{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.75rem;margin-right:6px;background:#1b4332}}
.pill.warn{{background:#5c4a00}}
.miss{{color:#ff7b72}}
</style></head><body>
<h1>V1C 尾帧接力 — 多轮链对比（漂移数据扩充实验）</h1>
<div class="meta">目的：单轮链（n=1）不足以判定「逐代漂移能否解决」。同链根（s01 尾帧）独立重跑接力链，
量化衔接 + 供逐镜人评漂移。指标=MAE 灰度 128（0-255，与历史 3.3-9.0 / 35-50 同刻度）。</div>

<h2>锚图</h2>
<div class="anchor">
<div>{f'<img src="{cover}" style="max-width:260px">' if cover else '<span class="miss">R3 cover 未解析</span>'}
<div class="note">狐狸 R3 校准参考（身份权威，look_2c6a01d45db5）</div></div>
<div>{img(root_tail)}<div class="note">s01 末帧=三条链共同链根（relay 参考来源）</div></div>
</div>

<h2>衔接 diff 矩阵（机器量化）</h2>
{''.join(matrix)}
<div class="meta">v1c_fallback 列=resolver bug 白跑批（首帧逐像素同 hero 开场）——历史事故批，数值上恰构成
「无接力」对照组；relay1/relay2=修复后独立接力链。s03 链接天然偏高（上镜尾=行走中态，开场重构图），
两条接力链一致复现（27.7/25.4），非回落信号。</div>
"""]

    for shot in CHAIN:
        parts.append(f"<h2>{shot} <span class='note'>{html.escape(RELAY_NOTE[shot])}"
                     f" ｜ 漂移观察：{html.escape(DRIFT_WATCH[shot])}</span></h2><div class='cols'>")
        for label, name, _r, _s in RUNS:
            d = runs_data[label].get(shot, {})
            dirname = DIR_ALIAS.get(label, label)
            base = f"recovered_v1c/{dirname}/{shot}_{dirname}.mp4"
            ff = f"_frames/{dirname}/{shot}_ff.png"
            tl = f"_frames/{dirname}/{shot}_tail.png"
            diff = (metrics.get(label) or {}).get("links", {}).get(shot)
            parts.append(f"""<div class="cand"><b>{html.escape(name)}</b>
<span class="pill warn">衔接 {diff if diff is not None else '—'}</span>
<span class="pill">{d.get('seconds', '—')}s</span><br>{vid(base)}
<div class="note">首帧 {img(ff)} 末帧 {img(tl)}</div>
<div class="note">version={html.escape(str(d.get('version_id') or '—'))}</div></div>""")
        parts.append("</div>")

    parts.append("""
<h2>人评（漂移数据扩充轮）</h2>
<div class="meta">逐镜 ⭐/✅/❌ + 备注，重点：①同镜跨链漂移是否复现（relay1 vs relay2 同镜对比）
②链位越深漂移越重（s04/s07 累计位）③与锚图的身份偏差方向（毛色/面部/体型/背景）。
你的每组建议 → 下一轮（缓解策略组）设计输入。</div>
</body></html>""")

    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"saved → {OUT} ({OUT.stat().st_size}B)")


if __name__ == "__main__":
    main()
