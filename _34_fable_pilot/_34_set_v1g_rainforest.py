# _34_fable_pilot/_34_set_v1g_rainforest.py — v1g《雨林奔跑》场景+镜头配置（全走正式 REST）。
# 1) 创建雨林 Location 2) 重绑 s02-s05 的场景实例 3) patch 雨林环境状态
# 4) refresh bindings 5) 写镜头描述/时长/参考
import io
import sys
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"


def api(method, path, **kw):
    r = httpx.request(method, f"{BASE}{path}", timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def main():
    # 0) 当前文档修订
    ov = api("GET", f"/api/project/{PROJ}/scene-system/overview")
    rev = ov.get("document_revision") or ov.get("revision")
    print("doc revision:", rev)

    # 1) 创建雨林 Location
    # 幂等：雨林 Location 已存在则直接用（重跑安全）
    lst = api("GET", f"/api/project/{PROJ}/scene-system/locations")
    llist = lst.get("locations") if isinstance(lst, dict) else lst
    rain = next((l for l in (llist or []) if "Rainforest" in str(l.get("name", ""))), None)
    if rain:
        loc_id = rain["location_id"]
        print("location exists:", loc_id)
    else:
        loc = api("POST", f"/api/project/{PROJ}/scene-system/locations", json={
            "name": "雨林 (Rainforest)",
            "description": "Dense tropical rainforest: towering trees, thick emerald canopy, "
                           "hanging vines and lianas, ferns and broad-leaf plants, a narrow dirt "
                           "trail winding through, occasional mist between trunks, distant bird calls",
            "identity_description": "Dense tropical rainforest with narrow dirt trail, towering trunks, "
                                    "vines, ferns, light shafts through canopy",
            "style_notes": "Same 2D storybook rendering as production; lush greens, warm light shafts",
            "expected_document_revision": rev,
        })
        loc_id = loc.get("location_id") or (loc.get("location") or {}).get("location_id")
        print("location created:", loc_id, str(loc)[:120])
    assert loc_id, "location id 未取到"

    # 2) 重绑 s02-s05 的场景实例 + 3) patch 雨林环境状态
    insts = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances")
    lst = insts.get("scene_instances") if isinstance(insts, dict) else insts
    by_scene = {i.get("story_scene_id"): i for i in (lst or [])}
    RF_STATE = {"time_of_day": "daytime", "weather": "humid, light mist",
                "lighting": "Shafts of sunlight piercing dense canopy, deep green shadows",
                "environment_conditions": "Humid air, faint mist between tree trunks, distant bird calls"}
    for scene_id in ("s02", "s03", "s04", "s05"):
        inst = by_scene.get(scene_id)
        if not inst:
            print(f"[{scene_id}] 无场景实例，跳过"); continue
        iid = inst["scene_instance_id"]
        r = api("PATCH", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}/location",
                json={"location_id": loc_id, "expected_revision": inst.get("revision") or rev})
        print(f"[{scene_id}] instance {iid} -> rainforest, changed={r.get('changed')}")
        # 重新读实例修订（换绑后 revision 变）
        insts = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances")
        lst = insts.get("scene_instances") if isinstance(insts, dict) else insts
        inst = next(i for i in (lst or []) if i["scene_instance_id"] == iid)
        r = api("PATCH", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}/state",
                json=dict(RF_STATE, expected_revision=inst.get("revision") or rev))
        print(f"[{scene_id}] state patched -> rainforest env")

    # 4) refresh bindings
    for sid in ("s02_c01", "s03_c01", "s04_c01", "s05_c01"):
        try:
            r = api("POST", f"/api/project/{PROJ}/shot-bindings/{sid}/refresh")
            print(f"[{sid}] binding refresh ->", str(r.get('status') or r)[:60])
        except Exception as e:
            print(f"[{sid}] binding refresh FAIL: {str(e)[:120]}")

    # 5) 镜头配置（预编剧 + 长度变量）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from src.core.store import ProjectStore
    from src.storyboard.structure_service import update_shot_fields
    store = ProjectStore(Path(__file__).resolve().parent.parent / "projects" / PROJ)
    CONFIG = {
        "s02_c01": {  # 5s 对照组：奔跑无收势 → 尾帧预期=迈步中（动态不稳定）
            "description": "热带雨林小径，狐狸奔跑入画并持续奔跑，穿行于树干与蕨叶之间，镜头侧面跟拍",
            "camera_angle": "side tracking",
            "composition": "中景侧面跟拍，雨林层次纵深",
            "target_duration_s": 5.0,
            "video_reference_asset_id": "ref_2ecaca2f3553",  # walk_side 身份锚（新环境无链根）
        },
        "s03_c01": {  # 10s 长度效果组：完整动作弧+收势 → 尾帧预期=站稳
            "description": "接上镜奔跑：狐狸加速，纵身跃过倒伏树干，落地后减速站稳，回头张望",
            "camera_angle": "side tracking",
            "composition": "中景侧面，倒伏树干为跳跃障碍物",
            "target_duration_s": 10.0,
            "video_reference_asset_id": "prev_tail",
        },
        "s04_c01": {  # 8s：站稳后继续深入
            "description": "狐狸转身继续深入雨林，奔跑跃过溪流岩石，落在空地放缓脚步",
            "camera_angle": "side tracking",
            "composition": "中景侧面，溪流岩石为跳跃障碍物",
            "target_duration_s": 8.0,
            "video_reference_asset_id": "prev_tail",
        },
        "s05_c01": {  # 5s：稳定收尾
            "description": "雨林空地，狐狸放慢脚步最后停下，回头张望，尾巴轻摆",
            "camera_angle": "eye level",
            "composition": "中景正面偏侧，空地开阔",
            "target_duration_s": 5.0,
            "video_reference_asset_id": "prev_tail",
        },
    }
    for shot_id, fields in CONFIG.items():
        project = store.load()
        result = update_shot_fields(project, shot_id, fields)
        store.save(project)
        print(f"[{shot_id}] config changed={result.get('changed')}")
    print("done: v1g 雨林配置写入")


if __name__ == "__main__":
    main()
