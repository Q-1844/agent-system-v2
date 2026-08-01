"""角色页 prompt 内容审核排查（阶段5③-e 附带发现）。

step1：原样重发角色页真实 prompt（routers.characters._build_view_prompt，front view），
       确认 400 content_policy_violation 是必现还是偶发。通过则=噪音，不动 prompt。
step2（若必现）：词语级二分排除--每次从 full prompt 移除一半短语，看是否变 PASS，
       PASS 则触发词在移除集里，递归缩到单个短语。最后验证 full-minus-trigger 是否 PASS。
不改 prompt 措辞，只定位是哪个词/短语踩雷。
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


async def send(client, prompt):
    """返回 (ok, info)。ok=True 表示生成成功；ok=False 表示 400/错误。"""
    try:
        res = await client.generate_image(prompt=prompt, size_tier="1K", ratio="2:3")
        return True, res.get("url") or "(no url)"
    except Exception as e:
        return False, str(e)[:200]


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
    # 用默认角色"小星"的描述（_init_default_characters 里的真实描述）
    desc = "a young girl with silver hair, blue eyes, wearing a white cloak, gentle expression"
    full = _build_view_prompt(desc, "anime", "front")
    phrases = [p.strip() for p in full.split(",") if p.strip()]
    print(f"full prompt ({len(phrases)} phrases):\n  {full}\n")

    # ---- step1：原样重发 ----
    ok, info = await send(client, full)
    print(f"step1 (原样重发): {'PASS' if ok else '400'} | {info[:120]}")
    if ok:
        print("=> 通过，上次 400 是噪音，不用动 prompt")
        return

    # ---- step2：必现，二分排除 ----
    print("\nstep2: 必现，二分排除定位触发词...")
    candidates = list(range(len(phrases)))   # 短语下标，触发词在其中
    while len(candidates) > 1:
        mid = len(candidates) // 2
        remove_idx = candidates[:mid]
        test = ", ".join(phrases[i] for i in range(len(phrases)) if i not in remove_idx)
        ok, _ = await send(client, test)
        removed = [phrases[i] for i in remove_idx]
        print(f"  remove {removed} -> {'PASS (trigger in removed)' if ok else '400 (trigger in kept)'}")
        if ok:
            candidates = remove_idx
        else:
            candidates = candidates[mid:]

    trigger = phrases[candidates[0]]
    print(f"\n=> 定位触发短语: '{trigger}'")

    # 验证：full minus trigger 是否 PASS
    verify = ", ".join(p for p in phrases if p != trigger)
    ok, _ = await send(client, verify)
    print(f"  验证 full-minus-trigger -> {'PASS (确认单一触发)' if ok else '400 (可能还有别的触发词，是组合)'}")


if __name__ == "__main__":
    asyncio.run(main())
