"""角色页尺寸验证探针（阶段5③-e，HANDOVER §八.2）。

垫片1/2 清理后，角色接口改用 size_tier/ratio。验证生成图实际尺寸符合角色页预期比例：
  - single/chain（2:3 竖图）-> 1K 2:3 = 832x1248
  - sheet（3:2 横图）       -> 1K 3:2 = 1248x832
旧 size="1024x1536" 经 Agnes 归一化本就返回 832x1248，新 API 直出 832x1248--尺寸不变，
只是去掉垫片间接层。本探针直接调 generate_image(size_tier, ratio) 核验比例。
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


async def gen_and_check(client, ratio, expected):
    # 用 benign prompt（尺寸由 size_tier/ratio 决定，与 prompt 内容无关；
    # "character/full body" 类 prompt 会被 Agnes 内容审核拒，这里只验尺寸）
    res = await client.generate_image(
        prompt="a single red apple on pure white background, product photo, centered",
        size_tier="1K", ratio=ratio)
    url = res.get("url")
    if not url:
        print(f"  ratio={ratio}  FAILED: no url in result {res}")
        return False
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
        r = await cli.get(url)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
    w, h = img.size
    ok = (w, h) == expected
    print(f"  ratio={ratio}  actual {w}x{h} | expected {expected[0]}x{expected[1]} | "
          f"{'PASS' if ok else 'FAIL'}")
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

    print(">>> 角色页尺寸验证（1K 2:3 竖图 + 1K 3:2 横图）")
    ok1 = await gen_and_check(client, "2:3", (832, 1248))   # single/chain
    ok2 = await gen_and_check(client, "3:2", (1248, 832))   # sheet
    print(f"\n总结: 2:3 {'PASS' if ok1 else 'FAIL'} | 3:2 {'PASS' if ok2 else 'FAIL'}")
    print("🔴 HANDOVER §八.2：不只看 HTTP 200，已实量生成图像素 = 角色页预期比例")


if __name__ == "__main__":
    asyncio.run(main())
