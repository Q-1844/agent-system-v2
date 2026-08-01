"""Agnes 内容审核 400 触发词定位工具（通用，复用二分排除逻辑）。

当 Agnes 图片接口返回 400 content_policy_violation，用二分排除定位是哪个短语/词触发：
  step1 原样重发确认必现（通过=噪音，不动 prompt）
  step2 必现则二分排除--每次从 full prompt 移除一半短语看是否变 PASS，
        PASS 则触发词在移除集，递归缩到单个短语，最后验证 full-minus-trigger。

用法（在项目根目录执行）：
  python scripts/probe_content_moderation.py                  # 内置案例：角色页 _build_view_prompt
  python scripts/probe_content_moderation.py "your prompt"    # 自定义 prompt

短语按 ", " 切分。以后 Agnes 策略变化导致类似 400，传新 prompt 直接复用，不用重写排查逻辑。
"""
import asyncio
import os
import sys
from pathlib import Path

# 让 scripts/ 下的脚本能 import 项目根的 routers/src（cwd 须在项目根）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml


def load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and not os.environ.get(k):
                os.environ[k] = v


async def send(client, prompt):
    """返回 (ok, info)。ok=True 生成成功；ok=False 400/错误。"""
    try:
        res = await client.generate_image(prompt=prompt, size_tier="1K", ratio="2:3")
        return True, res.get("url") or "(no url)"
    except Exception as e:
        return False, str(e)[:200]


def default_prompt():
    """内置案例：角色页 front view prompt（2026-08-01 定位触发词 'pure white background'）。"""
    from routers.characters import _build_view_prompt
    desc = "a young girl with silver hair, blue eyes, wearing a white cloak, gentle expression"
    return _build_view_prompt(desc, "anime", "front")


async def main():
    load_env()
    full = sys.argv[1] if len(sys.argv) > 1 else default_prompt()
    phrases = [p.strip() for p in full.split(",") if p.strip()]
    print(f"full prompt ({len(phrases)} phrases):\n  {full}\n")

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config" / "config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = dict(cfg["agnes"])
    if "${AGNES_API_KEY}" in str(agnes_cfg.get("api_key", "")):
        agnes_cfg["api_key"] = os.environ.get("AGNES_API_KEY", "")
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    from src.api.agnes_client import AgnesClient
    RateLimiter.get(agnes_cfg.get("rpm"))
    QuotaLedger.get_default(
        Path(__file__).resolve().parent.parent / "cache" / "quota_probe.json",
        limits=cfg.get("quota"))
    client = AgnesClient(agnes_cfg)

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
    verify = ", ".join(p for p in phrases if p != trigger)
    ok, _ = await send(client, verify)
    print(f"  验证 full-minus-trigger -> {'PASS (确认单一触发)' if ok else '400 (可能还有别的触发词，是组合)'}")


if __name__ == "__main__":
    asyncio.run(main())
