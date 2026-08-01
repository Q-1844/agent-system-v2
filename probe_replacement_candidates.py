"""测试 'pure white background' 替换候选（阶段5③-e 内容审核后续）。

对每个候选：把角色页 full prompt 里的 'pure white background' 替换为候选，发 1K 2:3，看过/拒。
设计隔离两层数据：
  - 'white background'(drop pure) 过 -> 'pure' 是雷
  - 'pure white backdrop'(保 pure 换 background) 过 -> 'background' 是雷
  - 都拒 -> 'white background' 组合敏感，需换 backdrop 变体
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


CANDIDATES = [
    "white background",       # drop "pure" -- 隔离 pure
    "plain white backdrop",
    "solid white background",
    "clean white backdrop",
    "pure white backdrop",    # 保 pure，换 background->backdrop -- 隔离 background
]


async def send(client, prompt):
    try:
        await client.generate_image(prompt=prompt, size_tier="1K", ratio="2:3")
        return True
    except Exception:
        return False


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

    from routers.characters import _build_view_prompt
    desc = "a young girl with silver hair, blue eyes, wearing a white cloak, gentle expression"
    full = _build_view_prompt(desc, "anime", "front")
    assert "pure white background" in full

    print(f"full prompt 里 'pure white background' 替换为候选，测 {len(CANDIDATES)} 个：\n")
    results = []
    for cand in CANDIDATES:
        prompt = full.replace("pure white background", cand)
        ok = await send(client, prompt)
        results.append((cand, ok))
        print(f"  {cand!r:28} -> {'PASS' if ok else '400'}")

    # 归因
    print("\n归因：")
    wb_pass = next((ok for c, ok in results if c == "white background"), None)
    pb_pass = next((ok for c, ok in results if c == "pure white backdrop"), None)
    if wb_pass:
        print("  'white background' PASS -> 'pure' 是雷（drop pure 即可，plain/solid/clean 同理应过）")
    elif pb_pass:
        print("  'pure white backdrop' PASS 但 'white background' 拒 -> 'background' 是雷（换 backdrop）")
    else:
        print("  两者都拒 -> 'white background' 组合敏感，需 backdrop 变体（plain/clean white backdrop）")


if __name__ == "__main__":
    asyncio.run(main())
