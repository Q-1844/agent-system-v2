"""Agnes .cn 图片接口探针（阶段5③-c 与垫片1/2 清理之间的最小成本探测）。

不改任何项目代码，仅用现有 AgnesClient 发一次最低档（1K 1:1）单图请求，确认：
  1. .cn 节点图片接口通不通（HTTP 200 + 返回 url）
  2. 返回图实际尺寸（验证归一化行为，FACTS §3.4.1 记过 1K 16:9->1312x736）
  3. 本地配额消耗（QuotaLedger images 桶 before/after）+ 配置的配额上限

🔴 真实计费（是否扣钱/免费额度）无法从 API 响应判断，须去 platform.agnes-ai.cn 计费页核对。
用独立 quota 文件（cache/quota_probe.json）避免和后端 cache/quota.json 冲突。
"""
import asyncio
import os
from pathlib import Path

import yaml


def load_env():
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and not os.environ.get(k):
                os.environ[k] = v


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
    # 独立 quota 文件，不碰后端的 cache/quota.json
    QuotaLedger.get_default(Path("cache/quota_probe.json"), limits=cfg.get("quota"))
    client = AgnesClient(agnes_cfg)

    qb = client.quota.snapshot() if client.quota else {}
    print(f"base_url       = {client.base_url}")
    print(f"image model    = {client.models['image']}")
    print(f"quota config   = {cfg.get('quota')}")
    print(f"quota before   = {qb}")
    print()

    prompt = "a single red apple on pure white background, product photo, centered, high detail"
    print(f">>> generate_image(size_tier='1K', ratio='1:1')  prompt={prompt[:50]}...")
    try:
        res = await client.generate_image(prompt=prompt, size_tier="1K", ratio="1:1")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {str(e)[:400]}")
        return

    print(f"result keys    = {list(res.keys())}")
    print(f"  url          = {res.get('url')}")
    print(f"  data[0]      = {res.get('data')}")
    qa = client.quota.snapshot() if client.quota else {}
    print(f"quota after    = {qa}")
    print()

    if res.get("url"):
        print(">>> download + 量实际尺寸")
        try:
            import httpx
            from PIL import Image
            import io
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
                r = await cli.get(res["url"])
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content))
                print(f"  HTTP {r.status_code} | content-type={r.headers.get('content-type')} | bytes={len(r.content)}")
                print(f"  实际尺寸 = {img.size[0]}x{img.size[1]} (mode={img.mode})")
        except Exception as e:
            print(f"  下载/量尺寸失败: {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
