# _34_fable_pilot/_34_build_gallery_v1b.py — 34.md V1B 对比 Gallery。
# 每镜：V1B（按镜开场参考）新版 + 第一轮候选折叠对比 + 机器注记。
import html
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
OUT = HERE / "FABLE_PILOT_V1B_GALLERY.html"

ASSIGN = {
    "s02_c01": ("walk_side 行走侧视", "ref_2ecaca2f3553"),
    "s03_c01": ("alert_gaze 仰头警觉", "ref_c19d9a0df2ec"),
    "s04_c01": ("crouch_ready 低伏蓄力", "ref_a1b93821b4a6"),
    "s05_c01": ("crouch_ready 低伏蓄力（与 s04 同参考衔接）", "ref_a1b93821b4a6"),
    "s06_c01": ("sit_look_up 坐地仰望", "ref_e5c261d74890"),
    "s07_c01": ("turned_away 背身离开", "ref_6bfdc88e57da"),
}
NOTES = {
    "s02_c01": "开场=行走侧视（原 R3 站姿）",
    "s03_c01": "开场=仰头警觉（原 R3 站姿）",
    "s04_c01": "开场=低伏（原 R3 站姿）",
    "s05_c01": "开场=低伏，与 s04 结尾衔接（首帧 diff 2.7=有意一致）",
    "s06_c01": "开场=坐地仰望（原 R3 站姿；落地反应镜衔接更自然）",
    "s07_c01": "开场=背身垂头离开；转身后脸部身份目检一致（机器⚠️未实际发生）",
}
R1_VERSIONS = {
    "s02_c01": ["vv_8274cab8", "vv_5f5fa7b9"],
    "s03_c01": ["vv_a4e9b9e6", "vv_64e01538"],
    "s04_c01": ["vv_1cc4ac6a", "vv_985d59de"],
    "s05_c01": ["vv_62bb72a3", "vv_ee3b6e36"],
    "s06_c01": ["vv_7f997201", "vv_0ebec72d"],
    "s07_c01": ["vv_76f5f38f", "vv_4ec98034"],
}


def main():
    v1b = {}
    for line in (HERE / "v1b_runs.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("job_status") == "done":
            v1b[r["shot_id"]] = r   # 每镜保留最新 done
    r1 = {}
    for line in (HERE / "candidates.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        r1.setdefault(r["shot_id"], []).append(r)

    parts = ["""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Fable Pilot V1B — 按镜开场参考对比</title>
<style>body{font-family:system-ui;background:#141414;color:#eee;max-width:1150px;margin:0 auto;padding:1.5rem}
h1{color:#ffd166}h2{color:#ffd166;margin-top:2rem}.meta{color:#aaa;font-size:.85rem}
.cand{background:#1e1e1e;border-radius:10px;padding:.9rem;margin:.8rem 0}
video{width:100%;max-width:700px;border-radius:8px;background:#000}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.75rem;margin-right:6px;background:#1b4332}
details{margin-top:.5rem}summary{cursor:pointer;color:#8ecae6;font-size:.85rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
</style></head><body>
<h1>Fable Pilot V1B — 按镜开场参考（新）vs 第一轮（旧）</h1>
<p class="meta">你的观察：8 狐镜首帧相同 → V1B=每镜一个开场姿态参考（全部从校准 R3 派生，同一只狐）。
量化：相邻镜首帧差异 35-50（旧版≈0）；s04↔s05 有意同参考（连续动作衔接，diff 2.7）。</p>
"""]
    for sid in ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]:
        label, aid = ASSIGN[sid]
        v = v1b.get(sid) or {}
        rel = f"../_34_fable_pilot/recovered_v1b/{sid}_V1B.mp4"
        parts.append(f"""<h2>{sid} — 开场参考：{html.escape(label)}</h2>
<p class="meta">{html.escape(NOTES[sid])} ｜ 参考资产 {aid}</p>
<div class="cand"><span class="pill">V1B 新版</span> <span class="meta">{html.escape(str(v.get('version_id')))} · {html.escape(str(v.get('seconds')))}s</span>
<br><video src="{html.escape(rel)}" controls preload="metadata"></video></div>
<details><summary>第一轮候选（开场=R3 站姿，用于对比）</summary>
<div class="row">""")
        for i, r in enumerate(r1.get(sid) or []):
            tag = chr(ord("A") + i)
            local = r.get("local_recovered") or ""
            rel1 = f"../_34_fable_pilot/recovered/{sid}_{tag}.mp4" if local else ""
            parts.append(f"""<div class="cand"><span class="pill">R1 {tag}</span>
<video src="{html.escape(rel1)}" controls preload="metadata"></video></div>""")
        parts.append("</div></details>")
    parts.append("""<p class="meta">评价口径同前：身份/四足解剖/葡萄稳定性/背景纯净/音频。
本页只看"开场姿态是否恰当 + 与上一镜结尾是否衔接"。逐镜结论发 Claude Code。</p>
</body></html>""")
    OUT.write_text("".join(parts), encoding="utf-8")
    print("v1b gallery:", OUT)


if __name__ == "__main__":
    main()
