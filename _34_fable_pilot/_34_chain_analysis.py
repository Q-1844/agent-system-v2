# _34_fable_pilot/_34_chain_analysis.py — 尾帧接力链历史整备+统一衔接量化。
# 1) run1 视频从 v1c_runs.jsonl 记录的 provider URL 重下（GCS 不过期）→ 独立存档
# 2) run2 从旧 recovered_v1c/*.mp4 归位 per-run 目录
# 3) 全 run 抽 ff/tail 帧 → 统一 MAE@128 灰度逐链接 diff → chain_metrics.json
# 用法: python _34_chain_analysis.py <run_id> ...   （缺省 r1+r2；r3 由 chain_run 自带量化）
import io
import json
import subprocess
import sys
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
OUT = HERE / "v1c_runs.jsonl"
CHAIN = ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]
ROOT_TAIL = HERE.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1" / \
    "output" / "videos" / "s01_c01.mp4"


def records():
    out = []
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def frame_extract(mp4, png, last=False):
    mp4, png = Path(mp4), Path(png)
    if not mp4.exists() or png.exists():
        return png if png.exists() else ""
    args = ["ffmpeg", "-y", "-v", "error"]
    if last:
        args += ["-sseof", "-0.1"]
    args += ["-i", str(mp4)]
    if not last:
        args += ["-vf", "select=eq(n\\,0)"]  # 输出选项必须在 -i 之后
    args += ["-frames:v", "1", str(png)]
    subprocess.run(args, capture_output=True)
    return str(png) if png.exists() else ""


def mae(a, b, w=128, h=128):
    from PIL import Image
    ia = Image.open(a).convert("L").resize((w, h))
    ib = Image.open(b).convert("L").resize((w, h))
    pa, pb = list(ia.getdata()), list(ib.getdata())
    return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)


def repair_run(run_id, src_round, pick="last"):
    """把某 run 的 6 条视频归位到 recovered_v1c/<run_id>/ 并确保本地存在。
    jsonl 同 shot 可能有多条 done（重试/复跑追加剧本）——每镜取第一条或最后一条。
    fallback 批与 relay1 同记 round='v1c'：fallback 取每镜第一条，relay1 取最后一条。"""
    d = HERE / "recovered_v1c" / run_id
    d.mkdir(parents=True, exist_ok=True)
    same = {}
    for r in records():
        if r.get("round") == src_round and r.get("job_status") == "done" and r.get("url"):
            if pick == "first":
                same.setdefault(r["shot_id"], r)
            else:
                same[r["shot_id"]] = r
    got = {}
    for shot in CHAIN:
        dest = d / f"{shot}_{run_id}.mp4"
        if not dest.exists():
            if run_id == "v1c_r2":
                old = HERE / "recovered_v1c" / f"{shot}_V1C.mp4"
                if old.exists():
                    old.replace(dest)
            else:
                url = (same.get(shot) or {}).get("url")
                if url:
                    try:
                        dest.write_bytes(httpx.get(url, timeout=300).content)
                        print(f"  [repair] {run_id}/{shot} downloaded ({dest.stat().st_size}B)")
                    except Exception as e:
                        print(f"  [repair] {run_id}/{shot} FAIL {str(e)[:80]}")
        got[shot] = dest if dest.exists() else None
    return got


def main():
    # 语义标签 → (jsonl round 名, 序数区间)。历史两条链同记 round='v1c'：
    # 前 6 条 = resolver bug 白跑批（hero 回落，充作无接力对照），后 6 条 = 接力链 1。
    # 历史两条链同记 round='v1c'（jsonl 单一顺序流，fallback 在前 relay1 在后）；
    # 修复后 repair_run 按「每镜第一条/最后一条 done」取数，序数区间废弃。
    SPECS = {"v1c_fallback": ("v1c", "first"), "v1c_relay1": ("v1c", "last"),
             "v1c_relay2": ("v1c_r3", "last")}
    run_ids = sys.argv[1:] or ["v1c_fallback", "v1c_relay1"]
    result = {}
    for run_id in run_ids:
        src_round, pick = SPECS.get(run_id, (run_id, "last"))
        vids = repair_run(run_id, src_round, pick)
        fr = HERE / "_frames" / run_id
        fr.mkdir(parents=True, exist_ok=True)
        links = {}
        root_tail = frame_extract(str(ROOT_TAIL), fr / "_s01_root_tail.png", last=True)
        prev = root_tail
        for i, shot in enumerate(CHAIN):
            mp4 = vids.get(shot)
            ff = frame_extract(str(mp4), fr / f"{shot}_ff.png") if mp4 else ""
            tail = frame_extract(str(mp4), fr / f"{shot}_tail.png", last=True) if mp4 else ""
            if ff and prev:
                links[shot] = round(mae(prev, ff), 1)
            prev = tail or prev
            if not tail:
                break
        result[run_id] = {"links": links,
                          "missing": [s for s, v in vids.items() if not v]}
        print(f"[{run_id}] links={links} missing={result[run_id]['missing']}")
    mf = HERE / "chain_metrics.json"
    existing = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    existing.update(result)
    mf.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved → {mf}")


if __name__ == "__main__":
    main()
