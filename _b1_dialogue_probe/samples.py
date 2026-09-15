# _b1_dialogue_probe/samples.py
# Native AV B1 E2E 样本定义（17.md §16：少量、有区分度）。
#
# 首帧复用 A0 探针已验证资产（cos-platform-outputs 公网 URL，不过期）——
# 同一样本 A/B 两侧共用同一首帧/模型/时长，唯一差异 = compiled prompt（§17）。
#
# 样本：
#   A 单角色普通对白 —— 台词准确性/中文稳定性/lip-sync 保持
#   B 单角色短句 —— 语言先验风险（超短中文句是否仍中文）
#   C 双角色三句 A→B→A —— speaker swap（本轮最重要，多 variant）
#   D 双角色仅一人说话 —— "谁应该沉默"控制
#
# dialogue 全部采用 Director 既有约定（src/agents/director.py:291：可多句、
# 可带说话人「角色名：台词」），C/D 的行内前缀即 §7.2 的解析辅助证据。
from __future__ import annotations

# A0 assets.json（_audio_probe/assets/assets.json）里的资产 key
ASSETS_JSON = "alice_speak alice_closeup alice_bob_door".split()

SAMPLES = [
    {
        "key": "A",
        "shot_id": "s01_c01",
        "title": "单角色普通对白",
        "goal": "台词准确性 / 中文稳定性 / 原生 lip-sync 保持",
        "image": "alice_speak",
        "frames": 193,          # ≈8s，长句（A0 DL 同档）
        "variants": 1,
    },
    {
        "key": "B",
        "shot_id": "s01_c02",
        "title": "单角色短句（语言先验风险）",
        "goal": "超短句是否仍目标语言；dialogue prompt 是否降低异常语言生成",
        "image": "alice_closeup",
        "frames": 121,          # ≈5s
        "variants": 1,
    },
    {
        "key": "C",
        "shot_id": "s02_c01",
        "title": "双角色三句 A→B→A（speaker swap 主样本）",
        "goal": "speaker swap 是否降低——本轮最重要样本",
        "image": "alice_bob_door",
        "frames": 193,          # ≈8s，三句（A0 J 组同档）
        "variants": 3,          # §16：多 variant，不只跑一次
    },
    {
        "key": "D",
        "shot_id": "s02_c02",
        "title": "双角色仅一人说话（沉默控制）",
        "goal": "「谁应该沉默」控制是否有效",
        "image": "alice_bob_door",
        "frames": 121,
        "variants": 1,
    },
]

# 项目内 Shot 创作字段（与 dialogue 真・一致；motion 只写可见视觉/动作）
CHARACTERS = [
    {"name": "林晚星",
     "description_en": "young woman lighthouse keeper, shoulder-length dark brown hair, beige trench coat, alert expression"},
    {"name": "顾岩",
     "description_en": "young man mechanic, black short hair, dark blue work jacket, calm face"},
    {"name": "小满",
     "description_en": "little girl, twin tails, red scarf, bright eyes"},
]

SHOTS = [
    {
        "shot_id": "s01_c01", "scene_id": "s01", "index": 1,
        "characters": ["林晚星"],
        "dialogue": "灯塔马上就要停电了，我们必须赶在暴风雨之前进去。",
        "description": "灯塔看守人房间内，林晚星看向桌上的油灯，神情急切，窗外暴风雨。",
        "motion": "她神情急切地看向前方，油灯火苗轻微摇曳。",
        "num_frames": 193,
    },
    {
        "shot_id": "s01_c02", "scene_id": "s01", "index": 2,
        "characters": ["林晚星"],
        "dialogue": "我们走。",
        "description": "林晚星面部特写，目光坚定，背景是昏暗的楼梯口。",
        "motion": "她微微点头，转身欲行。",
        "num_frames": 121,
    },
    {
        "shot_id": "s02_c01", "scene_id": "s02", "index": 1,
        "characters": ["林晚星", "顾岩"],
        "dialogue": "林晚星：「门开了吗？」\n顾岩：「还没有。」\n林晚星：「那就快一点。」",
        "description": "林晚星和顾岩站在一扇厚重的木门前，两人都看着门，油灯光影。",
        "motion": "两人站在门前，交替看向对方与木门。",
        "num_frames": 193,
    },
    {
        "shot_id": "s02_c02", "scene_id": "s02", "index": 2,
        "characters": ["小满", "林晚星"],
        "dialogue": "林晚星：灯塔！灯塔灭了！",
        "description": "小满和林晚星站在窗前，窗外灯塔的光熄灭，两人望向窗外。",
        "motion": "林晚星抬手指向窗外，小满在旁安静看着窗外。",
        "num_frames": 121,
    },
]


def build_spec_list() -> list[dict]:
    """→ [{spec_id, sample_key, side, variant, shot_id, frames, image}]。
    side: "A"=当前生产方式（flag off），"B"=B1 Dialogue+Speaker Control（flag on）。"""
    specs = []
    for s in SAMPLES:
        for side in ("A", "B"):
            for v in range(1, s["variants"] + 1):
                specs.append({
                    "spec_id": f"S{s['key']}_{side}{v:02d}",
                    "sample_key": s["key"], "side": side, "variant": v,
                    "shot_id": s["shot_id"], "frames": s["frames"],
                    "image": s["image"]})
    return specs


if __name__ == "__main__":
    for sp in build_spec_list():
        print(sp["spec_id"], sp["shot_id"], sp["frames"])
