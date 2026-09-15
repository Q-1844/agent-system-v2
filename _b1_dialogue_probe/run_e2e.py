# _b1_dialogue_probe/run_e2e.py
# Native AV B1 真机 E2E orchestrator（17.md §15-§17，scratch）。
#
# 设计（对齐 A0 run_tests.py 先例）：
#   - prompt 用**生产编译函数**实时编译（不是复制品）：
#       A 侧 = build_video_prompt(shot, "i2v")          —— 当前生产方式（flag off）
#       B 侧 = compile_prompt_with_native_dialogue(...) —— B1 flag on（同一函数、
#              同一 resolver，handler/adapter 生产路径原样调用）
#   - provider = 生产 AgnesClient.create_video（config 默认 v2.0，i2v，1280x704）
#   - A/B 同 shot / 同首帧（A0 已验证资产 URL）/ 同模型 / 同时长；唯一差异 = prompt
#   - MAX_OUTSTANDING=3 并发轮询；断点续跑（state.json + evidence 完整性）
#   - 每条证据：request.json（含 compiled prompt + speaker map）/create_response/
#     final_poll/video.mp4/ffprobe.json —— §13 的实验证据（非 production truth）
#   - 结束产出 evidence/review_manifest.json（评审网站数据源）
from __future__ import annotations
import asyncio
import sys
import time
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_common import (EVID, LANGUAGE, PROJECT_NAME, ev_dir, ffprobe_media,  # noqa: E402
                          jread, jwrite, load_state, make_client, save_state)
from samples import SAMPLES, build_spec_list  # noqa: E402

from src.core.prompt_builder import build_video_prompt  # noqa: E402
from src.core.store import ProjectStore  # noqa: E402
from src.generation.dialogue_control import (  # noqa: E402
    compile_prompt_with_native_dialogue,
    resolve_native_dialogue,
)

MAX_OUTSTANDING = 3
POLL_INTERVAL = 12.0
ASSETS_URL = Path(__file__).resolve().parent.parent / "_audio_probe" / "assets" / "assets.json"


def _compile_prompts(pdir: Path) -> dict:
    """生产编译函数实时编译 A/B 两侧 prompt + speaker map 证据。"""
    project = ProjectStore(pdir).load()
    out = {}
    for s in SAMPLES:
        shot = project.get_shot(s["shot_id"])
        assert shot is not None, f"shot 缺失: {s['shot_id']}（先跑 setup_project.py）"
        base = build_video_prompt(shot, "i2v")
        b_prompt = compile_prompt_with_native_dialogue(base, pdir, shot,
                                                       language=LANGUAGE)
        ctl = resolve_native_dialogue(pdir, shot, language=LANGUAGE)
        out[s["shot_id"]] = {
            "shot_id": s["shot_id"],
            "characters": list(shot.characters or []),
            "dialogue_truth": shot.dialogue or "",
            "dialogue_lines_truth": [ln for ln in (shot.dialogue or "").split("\n")
                                     if ln.strip()],
            "a_prompt_flag_off": base,
            "b_prompt_flag_on": b_prompt,
            "speaker_map": None if ctl is None else {
                "resolution": ctl.resolution,
                "lines": [{"index": ln.index, "text": ln.text,
                           "speaker": ln.speaker_character_name}
                          for ln in ctl.lines],
                "silent": list(ctl.silent_characters)},
        }
    return out


async def _collect(client, sp, assets, compiled):
    d = ev_dir(sp["spec_id"])
    state = load_state()
    st = state.get(sp["spec_id"], {})
    cd = compiled[sp["shot_id"]]
    prompt = cd["a_prompt_flag_off"] if sp["side"] == "A" else cd["b_prompt_flag_on"]
    image_url = (assets.get(sp["image"]) or {}).get("url")
    assert image_url, f"asset 缺 URL: {sp['image']}"
    t0 = time.time()
    if st.get("video_id") and not (d / "video.mp4").exists():
        video_id = st["video_id"]
    else:
        create = await client.create_video(
            prompt, mode="i2v", image_url=image_url,
            num_frames=sp["frames"], frame_rate=24, width=1280, height=704)
        video_id = create.get("video_id") or create.get("task_id") or create.get("id")
        jwrite(d / "request.json", {
            "spec": sp,
            "shot_id": sp["shot_id"],
            "model": "agnes-video-v2.0(config default)",
            "request_mode": "i2v",
            "image_asset": sp["image"],
            "image_url": image_url,
            "num_frames": sp["frames"],
            "language": LANGUAGE,
            "dialogue_truth": cd["dialogue_truth"],
            "speaker_map": cd["speaker_map"],
            "compiled_prompt": prompt,
        })
        jwrite(d / "create_response.json", create)
        state = load_state()
        state[sp["spec_id"]] = {"video_id": video_id, "submitted_at": time.time()}
        save_state(state)
        print(f"[{sp['spec_id']}] submitted video_id={video_id}", flush=True)

    poll = await client.poll_video(video_id, interval=POLL_INTERVAL)
    jwrite(d / "final_poll.json", poll["raw"])
    await client.download(poll["url"], d / "video.mp4")
    jwrite(d / "ffprobe.json", ffprobe_media(d / "video.mp4"))
    state = load_state()
    state[sp["spec_id"]] = {"video_id": video_id, "status": "done",
                            "done_at": time.time()}
    save_state(state)
    print(f"[{sp['spec_id']}] DONE {poll.get('seconds')}s "
          f"({round(time.time() - t0)}s wall)", flush=True)


def _done(sp) -> bool:
    d = ev_dir(sp["spec_id"])
    return (d / "video.mp4").exists() and (d / "ffprobe.json").exists()


def build_manifest(compiled) -> dict:
    assets = jread(ASSETS_URL, {}) or {}
    items = []
    for s in SAMPLES:
        cd = compiled[s["shot_id"]]
        side_items = []
        for sp in build_spec_list():
            if sp["sample_key"] != s["key"]:
                continue
            side_items.append({
                "spec_id": sp["spec_id"],
                "side": "A" if sp["side"] == "A" else "B",
                "side_label": ("当前生产方式（无台词控制）" if sp["side"] == "A"
                               else "B1 台词+说话人控制"),
                "variant": sp["variant"],
                "video": f"evidence/{sp['spec_id']}/video.mp4",
                "prompt_preview": (cd["a_prompt_flag_off"] if sp["side"] == "A"
                                   else cd["b_prompt_flag_on"]),
            })
        items.append({
            "key": s["key"], "title": s["title"], "goal": s["goal"],
            "shot_id": s["shot_id"],
            "first_frame": (assets.get(s["image"]) or {}).get("url", ""),
            "dialogue_truth": cd["dialogue_truth"],
            "expected_speaker_sequence": cd["speaker_map"],
            "items": sorted(side_items, key=lambda x: (x["side"], x["variant"])),
        })
    return {"language": LANGUAGE, "samples": items}


async def main():
    pdir = Path(__file__).resolve().parent.parent / "projects" / PROJECT_NAME
    assert (pdir / "output" / "project.json").exists(), \
        "项目不存在——先运行 setup_project.py"
    compiled = _compile_prompts(pdir)
    jwrite(EVID / "compiled_prompts.json", compiled)

    specs = build_spec_list()
    client, ledger = make_client()
    assets = jread(ASSETS_URL, {}) or {}
    pending = [sp for sp in specs if not _done(sp)]   # _collect 自带断点续跑
    print(f"total={len(specs)} pending={len(pending)}", flush=True)

    sem = asyncio.Semaphore(MAX_OUTSTANDING)
    failures: list[tuple[str, str]] = []

    async def worker(sp):
        async with sem:
            try:
                await _collect(client, sp, assets, compiled)
            except Exception as e:
                failures.append((sp["spec_id"], f"{type(e).__name__}: {e}"[:300]))
                print(f"[{sp['spec_id']}] FAIL {type(e).__name__}: {str(e)[:200]}",
                      flush=True)

    await asyncio.gather(*(worker(s) for s in pending))
    await client.aclose()

    manifest = build_manifest(compiled)
    jwrite(EVID / "review_manifest.json", manifest)
    snap = ledger.snapshot()
    print(f"ledger={snap} failures={len(failures)}")
    for fid, err in failures:
        print(f"  FAIL {fid}: {err}")
    if not failures:
        print("review manifest -> evidence/review_manifest.json")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
