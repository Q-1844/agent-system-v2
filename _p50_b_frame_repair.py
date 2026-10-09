# -*- coding: utf-8 -*-
"""Phase50 B 首帧修复（执行侧介入，账本记录）：重建 5 张硬错误帧 + 补 4 张缺失帧。

用法：python _p50_b_frame_repair.py gen   （生成到 _p50_b_repair/）
      python _p50_b_frame_repair.py attach （自检通过后追加候选并选定）
"""
import asyncio
import json
import os
import sys
import io
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

OUT = Path("_p50_b_repair")

# 每帧：shot_id, 用到的 look 参考（按 URL），提示词
BOAT = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_Y7K4sqXAL6RWaNdBxMmZwGe18QNhwcNH/output.png"
CAPT = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_aF380vb3j2t9Y0f1ozdDKzD"  # 占位，运行时从 resources.json 读
MATE = ""
MONS = ""

FRAMES = []


def load_refs():
    global CAPT, MATE, MONS
    r = json.load(open("projects/P50_B/output/resources.json", encoding="utf-8"))
    ras = r.get("reference_assets") or {}
    def u(aid):
        return ((ras.get(aid) or {}).get("url") or "")
    CAPT = u("ref_e5e8b8e0a54c")
    MATE = u("ref_e8858d5baf12")
    MONS = u("ref_9c0ba4bed38c")


def build_frames():
    return [
        ("s01_c05", [CAPT],
         "Photorealistic cinematic frame on a small navy-hulled wooden fishing boat at dusk: "
         "ONLY ONE PERSON aboard — the same weathered older fisherman (grey-brown hair, navy oilskin "
         "jacket, rope belt) grips the wooden ship's wheel with both hands and yanks it hard to port, "
         "body twisting with the turn, boat heeling, spray over the bow, open sea horizon, warm dusk "
         "light. Exactly one human. No dock, no pier, no other people, no other boats."),
        ("s02_c02", [CAPT],
         "Photorealistic cinematic close shot at the stern steering position of a small navy fishing "
         "boat at dusk: ONLY ONE PERSON — the same weathered older fisherman (navy oilskin jacket, "
         "rope belt) slams the throttle lever to full and holds the shaking wheel, body leaning "
         "forward against the vibration, sweat on his forehead, black exhaust smoke pouring from the "
         "stack, wake stretching behind. Exactly one human. No other people."),
        ("s02_c04", [BOAT],
         "Photorealistic cinematic wide shot at dusk: the same small navy-hulled wooden fishing boat "
         "speeding left-to-right with black exhaust smoke and a long white wake. BEHIND the boat, "
         "beneath the surface only, a vast dark shadow glides after it with faint huge tentacle "
         "outlines just under the water and scattered bubbles. The creature is entirely SUBMERGED — "
         "nothing above the waterline except the boat. No other vessels, no people visible, "
         "ominous dusk light."),
        ("s03_c01", [],
         "Photorealistic cinematic shot at dusk, camera low over the sea surface: giant tentacle "
         "outlines glide just beneath the dark water toward the viewer, suction cups and barnacle "
         "texture faintly visible through the surface, bubbles breaking in rows, the sea swelling "
         "unnaturally. No boat, no people, no creature above the water, no ships on the horizon, "
         "clear dusk sky."),
        ("s03_c03", [MATE],
         "Photorealistic cinematic frame on the tilted wooden deck of a small navy fishing boat at "
         "dusk: ONLY ONE PERSON — the same young fisherman (dark hair, grey work jacket) thrown "
         "against the base of the mast, one hand clamped around it, knees sliding on the sloping "
         "wet planks, a fishing net scattered beside him, spray in the air. He is ON the deck, "
         "not in the water. Exactly one human. No other people."),
        ("s05_c01", [BOAT, MATE],
         "Photorealistic cinematic frame on the same navy-hulled wooden fishing boat at early night: "
         "ONLY ONE PERSON — the young fisherman (grey work jacket) crouches by the starboard gunwale "
         "bailing seawater off the deck with a battered bucket, a deep fresh scrape mark along the "
         "planks weeping water, mildew lamp glow from the cabin. Exactly one human. No other people, "
         "calm sea, distant lights on the horizon."),
        ("s04_c01", [BOAT],
         "Photorealistic cinematic wide shot at dusk: the same small navy fishing boat heels into a "
         "hard turn toward a reef passage — dark rocks break the surface ahead with white water "
         "streaming through gaps, spray blows off the bow. The boat is small in frame, one tiny "
         "figure at the stern wheel. No monster visible, no other vessels. Tense dusk light."),
        ("s04_c02", [MATE],
         "Photorealistic cinematic frame on the foredeck of the small navy fishing boat inside a "
         "reef channel at dusk: ONLY ONE PERSON — the young fisherman (grey work jacket) hurls a "
         "coiled line with all his strength toward dark rocks beside the boat, rope uncoiling in "
         "the air, boat lurching, spray. Exactly one human. No other people."),
        ("s04_c04", [],
         "Photorealistic cinematic wide shot at dusk: a small navy fishing boat slips through a "
         "shallow reef channel in mid-frame, rocks and white water on both sides. Behind it at the "
         "reef edge, huge tentacles slap the shallow water and rocks, unable to follow, big spray "
         "bursts — the creature cannot enter the shallows. No people visible, no other vessels, "
         "the boat escaping toward open water."),
    ]


async def gen():
    load_refs()
    OUT.mkdir(exist_ok=True)
    from src.pipeline.controller import PipelineController
    client = PipelineController().agnes_client
    manifest = {}
    for shot_id, refs, prompt in build_frames():
        refs = [u for u in refs if u]
        try:
            res = await client.generate_image(prompt=prompt, image_refs=refs or None,
                                              size_tier="1K", ratio="16:9")
            url = res.get("url") or ""
            b64 = res.get("b64")
            p = OUT / f"{shot_id}.png"
            if url:
                import urllib.request
                p.write_bytes(urllib.request.urlopen(url, timeout=120).read())
                manifest[shot_id] = {"url": url, "local": str(p), "refs": refs}
                print(shot_id, "OK", url[:80])
            elif b64:
                import base64
                p.write_bytes(base64.b64decode(b64))
                manifest[shot_id] = {"url": "", "local": str(p), "refs": refs, "b64": True}
                print(shot_id, "OK(b64)")
            else:
                print(shot_id, "NO OUTPUT", str(res)[:120])
        except Exception as e:
            print(shot_id, "ERR", str(e)[:140])
    (OUT / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def attach():
    """自检后的帧追加为候选并选定（执行侧修复，账本记录）。"""
    from src.core.store import ProjectStore
    from src.core.models import Candidate, ImageBatch
    from src.core.timing_ledger import record
    import time as _t
    manifest = json.load(open(OUT / "_manifest.json", encoding="utf-8"))
    approved = json.load(open(OUT / "_approved.json", encoding="utf-8"))
    store = ProjectStore(Path("projects/P50_B"))
    for shot_id in approved:
        m = manifest.get(shot_id)
        if not m:
            print(shot_id, "no manifest, skip")
            continue
        url = m.get("url") or ""
        local = m.get("local")
        cid = f"ff_fix_{shot_id.replace('_','')}"
        def mut(s, cid=cid, url=url, local=local):
            batch = ImageBatch(batch_id=f"ffb_fix_{shot_id}", created_at=_t.time(),
                               candidates=[Candidate(candidate_id=cid, local=local or "",
                                                     url=url, status="done")])
            s.first_frame_batches.append(batch)
            s.first_frame.candidates = [c for c in s.first_frame.candidates]
            # 老字段 candidates 结构为 Asset；追加一个 done 候选并选择
            from src.core.models import Asset
            s.first_frame.candidates.append(Asset(local=local or "", url=url, status="done"))
            s.first_frame.selected_index = len(s.first_frame.candidates) - 1
            s.first_frame.status = "done"
            s.selected_first_frame = cid
        store.update_shot(shot_id, mut)
        record("manual_intervention", phase="P50_B", kind="first_frame_repair",
               shot_id=shot_id, candidate_id=cid, url=url[:120],
               detail="执行侧重建首帧（硬错误修复/缺失补齐），人检通过后选定")
        print(shot_id, "attached+selected", cid)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gen"
    if cmd == "gen":
        asyncio.run(gen())
    elif cmd == "attach":
        attach()
