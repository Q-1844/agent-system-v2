# -*- coding: utf-8 -*-
"""33.md 参考图制作：R1 白底角色板（t2i）→ R2/R3 以 i2i 从 R1 派生（构造性同狐）。

幂等：已存在且校验通过的参考跳过。产物 _33_fox_calibration/references/。
"""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

E33 = ROOT / "_33_fox_calibration"
REFS = E33 / "references"
REFS.mkdir(parents=True, exist_ok=True)

FOX_DESIGN = (
    "A natural quadruped red fox with red-orange fur, white muzzle and chest, "
    "dark brown-black lower legs, large expressive pointed ears, amber eyes, "
    "and a full bushy tail with a pale tip. Realistic stable fox body proportions. "
    "A small distinctive notch in its left ear. No clothing, no accessories."
)
STYLE_LINE = ("Cinematic storybook animation, stylized 2D animated look. "
              "Do not render as live-action photography or 3D rendered CGI.")

R1_PROMPT = (FOX_DESIGN + " Single small character centered, pure white backdrop, "
             "full body, standing straight, lots of white space around the character, "
             "the character takes up 40-50% of the image height, high quality, "
             "detailed character design. " + STYLE_LINE)
R2_PROMPT = (FOX_DESIGN + " Single fox only, no text, no letters, soft neutral "
             "light-grey gradient background, standing naturally in a three-quarter "
             "view facing slightly left of camera, full body visible with clear margin "
             "around it, the fox fills about 70% of the frame height, even soft "
             "lighting, relaxed standing pose, all four legs visible, tail resting "
             "naturally. " + STYLE_LINE)
R3_PROMPT = (FOX_DESIGN + " The fox stands on a dirt path in a quiet vineyard, a few "
             "hanging grape clusters on the vines behind it, cinematic soft warm "
             "daylight, full body visible, clearly the focus of the frame, no other "
             "subjects, simple uncluttered composition, three-quarter view. "
             + STYLE_LINE)

R1_SEED = 777   # 图片接口 seed 上限 999（2026-09-21 实测 400：seed 必须在 -1 到 999 之间）
SIZE_TIER, RATIO = "2K", "16:9"


async def _gen(client, name, prompt, *, image_refs=None, seed=None):
    dest = REFS / f"{name}.png"
    prov_file = REFS / f"{name}.provenance.json"
    if dest.exists() and prov_file.exists():
        print(f"[{name}] exists, skip")
        return json.loads(prov_file.read_text(encoding="utf-8"))
    t0 = time.time()
    r = await client.generate_image(prompt, image_refs=image_refs,
                                    size_tier=SIZE_TIER, ratio=RATIO, seed=seed)
    url, b64 = r.get("url"), r.get("b64")
    raw = None
    if b64:
        import base64
        raw = base64.b64decode(b64)
    elif url:
        raw = await client.download_bytes(url) if hasattr(client, "download_bytes") else None
        if raw is None:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=120).read()
    assert raw, f"[{name}] no image bytes"
    dest.write_bytes(raw)
    prov = {
        "name": name, "mode": "i2i" if image_refs else "t2i",
        "prompt": prompt, "seed": seed,
        "image_refs": [u if isinstance(u, str) else u for u in (image_refs or [])],
        "provider_url": url, "local": str(dest), "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_tier": SIZE_TIER, "ratio": RATIO, "elapsed_s": round(time.time() - t0, 1),
    }
    prov_file.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{name}] done {len(raw)}B url={str(url)[:60]} {prov['elapsed_s']}s", flush=True)
    return prov


async def main():
    from src.core.quota import QuotaLedger
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    QuotaLedger.get_default(ROOT / "cache" / "quota.json", limits=cfg.get("quota"))
    from _31_e2e_driver import load_env, setup_clients
    load_env()
    client = setup_clients()

    p1 = await _gen(client, "R1_plate", R1_PROMPT, seed=R1_SEED)
    r1_url = p1.get("provider_url")
    assert r1_url, "R1 无公网 URL，无法 i2i 派生"
    await _gen(client, "R2_neutral", R2_PROMPT, image_refs=[r1_url])
    await _gen(client, "R3_cinematic", R3_PROMPT, image_refs=[r1_url])
    print("all references ready ->", REFS)


if __name__ == "__main__":
    asyncio.run(main())
