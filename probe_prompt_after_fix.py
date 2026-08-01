"""换 'pure white backdrop' 后验证三处 prompt（阶段5③-e 收尾）。

每处测一次：发 t2i 确认不再 400，下载图查四角是否近白（背景观感代理）。
"""
import asyncio
import io
import os
from pathlib import Path

import httpx
import yaml
from PIL import Image


def load_env():
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and not os.environ.get(k):
                os.environ[k] = v


async def send_and_check(client, prompt, ratio, label):
    try:
        res = await client.generate_image(prompt=prompt, size_tier="1K", ratio=ratio)
    except Exception as e:
        print(f"  {label}: 400 | {str(e)[:120]}")
        return False
    url = res.get("url")
    if not url:
        print(f"  {label}: PASS but no url")
        return False
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
        r = await cli.get(url)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    w, h = img.size
    corners = [img.getpixel((5, 5)), img.getpixel((w - 6, 5)),
               img.getpixel((5, h - 6)), img.getpixel((w - 6, h - 6))]
    avg = sum(sum(c) / 3 for c in corners) / 4
    ok = avg >= 245
    print(f"  {label}: PASS | {w}x{h} | corners-avg={avg:.0f} "
          f"{'(white OK)' if ok else '(NOT white!)'}")
    return ok


async def main():
    load_env()
    cfg = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = dict(cfg["agnes"])
    if "${AGNES_API_KEY}" in str(agnes_cfg.get("api_key", "")):
        agnes_cfg["api_key"] = os.environ.get("AGNES_API_KEY", "")
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    from src.api.agnes_client import AgnesClient
    RateLimiter.get(agnes_cfg.get("rpm"))
    QuotaLedger.get_default(Path("cache/quota_probe.json"), limits=cfg.get("quota"))
    client = AgnesClient(agnes_cfg)

    from routers.characters import (
        _build_view_prompt, _build_img2img_prompt, _build_sheet_prompt)
    desc = "a young girl with silver hair, blue eyes, wearing a white cloak, gentle expression"
    print(">>> 三处 prompt 换 'pure white backdrop' 后验证\n")
    await send_and_check(client, _build_view_prompt(desc, "anime", "front"), "2:3",
                         "_build_view_prompt (front)")
    await send_and_check(client, _build_img2img_prompt(desc, "anime", "side"), "2:3",
                         "_build_img2img_prompt (side)")
    await send_and_check(client, _build_sheet_prompt(desc, "anime"), "3:2",
                         "_build_sheet_prompt")


if __name__ == "__main__":
    asyncio.run(main())
