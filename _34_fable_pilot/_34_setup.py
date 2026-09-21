# _34_fable_pilot/_34_setup.py — Pilot 项目 setup（34.md §4；setup-only 授权范围
# 与 Phase D §34 同口径：project creation + VisualProfile 初始选择 + size_tier=2K。
# 故事/角色/场景/镜头全部由 Producer→Specialist 生产，本脚本零其他 canonical 写入）。
import io, json, sys
from pathlib import Path
import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "..")
BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"

ART_DIRECTION = ("Cinematic storybook animation in the tradition of classic fable "
                 "illustration; warm natural light, clean silhouettes, gentle "
                 "stylization, expressive animal acting.")
STYLE_TAGS = ["storybook animation", "cinematic lighting", "clean silhouettes",
              "expressive animal acting", "warm color palette"]
NEGATIVE = ["photorealistic textures", "text or watermark in frame",
            "extra limbs or duplicated characters"]

def main():
    r = httpx.post(f"{BASE}/api/projects/create", params={
        "name": PROJ, "render_family": "2d_animation",
        "art_direction": ART_DIRECTION}, timeout=30)
    print("create:", r.status_code, r.text[:200])
    if r.status_code not in (200, 409):
        raise SystemExit("create failed")
    r = httpx.get(f"{BASE}/api/project/{PROJ}/visual-profile", timeout=30)
    cur = r.json()
    body = {"render_family": "2d_animation", "art_direction": ART_DIRECTION,
            "style_tags": STYLE_TAGS, "negative_style_constraints": NEGATIVE,
            "expected_revision": cur.get("revision") or ""}
    r = httpx.put(f"{BASE}/api/project/{PROJ}/visual-profile", json=body, timeout=30)
    print("profile put:", r.status_code, r.text[:200])
    # setup-only technical data：2K 生产档（R3 参考与场景参考的生成档位；Phase D §34 先例）
    from src.core.store import ProjectStore
    pdir = Path("../projects") / PROJ
    store = ProjectStore(pdir)
    def _mut(p):
        p.style.size_tier = "2K"
    store.update_project(_mut)
    print("size_tier set: 2K")

if __name__ == "__main__":
    main()
