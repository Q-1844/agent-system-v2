# _c1_native_av_e2e/samples.py
# Native AV C1 真机 E2E 样本定义（18.md §33 A-H：8 个高信息量样本）。
#
# 路径：A-F/H = UNBOUND 生产路径（_handler_video 经 JobQueue）；
#       G = BOUND 生产路径（完整 binding + BOUND 首帧 + BOUND 视频）。
# 首帧：UNBOUND 复用 A0 已验证资产 URL（B1 先例）；G 经生产 _handler_first_frame
#       真实生成（boundary frame + generation_basis）。
from __future__ import annotations

PROJECT_NAME = "__native_av_c1_e2e"
BOUND_PROJECT_NAME = "__shot_binding_visual_final_gp"   # S3C.2 golden path 项目（BOUND 基建在盘）
LANGUAGE = "Mandarin Chinese"

# A0 已验证资产（_audio_probe/assets/assets.json 的 key）
ASSET_A = "alice_speak"       # 单角色近景说话
ASSET_B = "alice_wide"        # 单角色全景
ASSET_C = "alice_bob_door"    # 双人门前
ASSET_D = "alice_bob_door"    # 双人（仅一人说话）
ASSET_E = "alice_room"        # 有木门的房间（动作音）
ASSET_F = "alice_corridor"    # 走廊（B1 时期易自动 BGM 的氛围镜）
ASSET_H = "alice_climb"       # 单角色动作中说话

# ============ UNBOUND 项目（主项目）角色与镜头 ============
CHARACTERS = [
    {"name": "林晚星", "description_en": "a young female lighthouse keeper in her "
     "early 20s with dark brown shoulder-length hair, wearing a beige trench coat"},
    {"name": "顾岩", "description_en": "a tall male engineer in his late 20s with "
     "short black hair, wearing a dark work jacket"},
]

# dialogue 全按 Director 既有约定（可带说话人前缀）；H 含舞台指示（§7 格式）
SHOTS = [
    {"key": "A", "shot_id": "s01_c01", "scene_id": "s01", "index": 1,
     "asset": ASSET_A, "characters": ["林晚星"],
     "description": "林晚星在灯塔看守人房间内，面向镜头方向，神情紧张地说话",
     "motion": "林晚星微微摇头，双手紧握在胸前，目光恳切",
     "dialogue": "林晚星：灯塔的灯不能灭，这是整片海域唯一的光源，我们绝不能就这么离开。",
     "seconds": 6},
    {"key": "B", "shot_id": "s01_c02", "scene_id": "s01", "index": 2,
     "asset": ASSET_B, "characters": ["林晚星"],
     "description": "林晚星站在走廊尽头，转身看向来人，果断开口",
     "motion": "林晚星转身，下颌微抬",
     "dialogue": "林晚星：我们走。",
     "seconds": 4},
    {"key": "C", "shot_id": "s02_c01", "scene_id": "s02", "index": 1,
     "asset": ASSET_C, "characters": ["林晚星", "顾岩"],
     "description": "林晚星与顾岩站在厚重的木门前，交替看向对方与门",
     "motion": "两人对视，林晚星先开口，顾岩回应后林晚星再追问",
     "dialogue": "林晚星：「门开了吗？」\n顾岩：「还没有。」\n林晚星：「那就快一点。」",
     "seconds": 8},
    {"key": "D", "shot_id": "s02_c02", "scene_id": "s02", "index": 2,
     "asset": ASSET_D, "characters": ["林晚星", "顾岩"],
     "description": "林晚星与顾岩并排站立，林晚星望向远处开口，顾岩沉默看着她",
     "motion": "林晚星说话时目光望向远方，顾岩保持沉默只是看着她",
     "dialogue": "林晚星：风暴明天才会来，我们还有时间。",
     "seconds": 5},
    {"key": "E", "shot_id": "s03_c01", "scene_id": "s03", "index": 1,
     "asset": ASSET_E, "characters": ["顾岩"],
     "description": "顾岩推开看守人房间的厚重木门，门轴发出声响，他停顿后开口",
     "motion": "顾岩推门，门缓缓打开，他停顿一拍，转过头",
     "dialogue": "顾岩：这扇门每晚都要上油，不然整层楼都听得见。",
     "audio_intent": {"music_policy": "none",
                      "directives": ["heavy wooden door creaks as it opens",
                                     "floorboards creak softly under footsteps"]},
     "seconds": 6},
    {"key": "F", "shot_id": "s03_c02", "scene_id": "s03", "index": 2,
     "asset": ASSET_F, "characters": ["林晚星"],
     "description": "深夜走廊，林晚星独自缓步前行，低声自语",
     "motion": "林晚星缓步前行，手抚过墙壁，低声说话",
     "dialogue": "林晚星：这里安静得能听见自己的心跳，小满以前总说这样的夜里能听见海。",
     "seconds": 6},
    {"key": "H", "shot_id": "s04_c01", "scene_id": "s04", "index": 1,
     "asset": ASSET_H, "characters": ["林晚星", "顾岩"],
     "description": "林晚星攀在梯架上回头，顾岩在下方扶住梯子，两人简短交流",
     "motion": "林晚星攀爬中回头说话，顾岩在下方抬头回应",
     "dialogue": "（林晚星攀在铁梯上，梯架微微晃动）\n林晚星：（喘着气）再往上二十级就到了。\n顾岩：（在下方扶住梯架）我数着，你慢慢来。",
     "seconds": 6},
]

# ============ BOUND 项目（样本 G）============
BOUND_SHOT = {"shot_id": "s01_c01", "scene_id": "s01", "index": 1,
              "characters": ["Alice"],
              "description": "Alice pauses at the steel door and speaks without "
              "turning around",
              "opening_state": "Alice standing near the steel door",
              "motion": "Alice looks back over her shoulder and speaks",
              "dialogue": "Alice：门后的东西在动，我们得马上离开这里。",
              "seconds": 6}


def unbound_spec_list():
    """(sample_key, shot_id) 列表——每样本 1 条视频（生产路径 1 variant）。"""
    return [(s["key"], s["shot_id"]) for s in SHOTS]
