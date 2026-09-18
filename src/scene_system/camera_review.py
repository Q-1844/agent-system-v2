# src/scene_system/camera_review.py
# Agent System V2 Phase C2 Work A（28.md §2）：LocationView / SceneInstanceView 审查的
# 机位正确性加固（camera correctness hardening）。
#
# 背景（28.md §2 / Golden Run 实证）：某些声明为 overhead / side / rear 的 view 虽然审查
# PASS，但相机实际仍接近 hero/front。根因：旧审查 prompt 只有一句 "viewed from the X
# looking toward the Y"，没有任何机位语义；模型容易把「指定 anchor 出现在画面里」
# 等同于「指定 camera orientation 已满足」（feature checklist 假视角）。
#
# 🔴 分离两件事（§2.2）：A. 地点身份一致（location_identity 等维度）与
#    B. 相机位置/朝向正确（本模块）。anchor visibility ≠ camera correctness。
# 🔴 不重构 Review schema（§2.2）：view_consistency 维度本身已存在且是 hard 维度——
#    本模块只（1）强化 prompt 机位语义；（2）要求模型先报告「实际观察到的机位」；
#    （3）对报告出的机位做确定性对照推导。schema 只加两个平铺字段（additive）。
# 🔴 诚实原则（§2.4）：模型没报告可用的机位证据 → pass 一律封顶为 uncertain（fail
#    closed，与 view_review 既有「维度缺失不判 pass」纪律同族）；观察机位与请求机位
#    明确矛盾 → fail。模型做不到 rear/side/nadir → needs_fix，绝不改判卷放水。
# 🔴 确定性（同域纪律）：同输入 → 同输出；无时间/随机/LLM 参与。
from __future__ import annotations
from typing import List, Optional, Tuple

# 观察机位的合法取值（审查模型必须从中选一报告）
OBSERVED_ZONES = ("front", "side", "rear", "overhead", "other", "uncertain")

# 请求侧 camera_zone 的语义词元（token 精确匹配，非子串——"countryside"/"hilltop"
# 不会误命中 side/top）。compass 式自由串（如 south_west）无词元命中 → 返回 None：
# 无法确定性验证的机位交给强化后的模型判卷（仍然要求报告 observed + evidence）。
_ZONE_GROUP_TOKENS = {
    "front": {"front"},
    "rear": {"rear", "back"},
    "side": {"side", "left", "right", "lateral", "profile", "flank"},
    "overhead": {"overhead", "top", "down", "topdown", "above", "nadir",
                 "bird", "aerial", "plan"},
}


def _tokens(zone: str) -> List[str]:
    out: List[str] = []
    cur = []
    for ch in str(zone or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def camera_zone_group(zone: str) -> Optional[str]:
    """请求侧 camera_zone → 语义机位组（front|rear|side|overhead）；无词元命中 → None。
    多词元命中不同组时按 overhead > rear > side > front 优先（俯视是最容易被稀释的
    声明，优先保护其确定性检查）。"""
    toks = set(_tokens(zone))
    if not toks:
        return None
    for g in ("overhead", "rear", "side", "front"):
        if toks & _ZONE_GROUP_TOKENS[g]:
            return g
    return None


# ============ prompt 机位语义块（两处审查共用同一语义）============
def camera_expectation_block(camera_zone: str, look_direction: str) -> str:
    """审查 prompt 的 CAMERA CORRECTNESS 块。camera_zone/look_direction 为空时给通用
    版本（仍要求报告 observed——机位是 production reference 的核心承诺）。"""
    req: List[str] = []
    if str(camera_zone or "").strip():
        req.append(f"requested camera position/zone = \"{str(camera_zone).strip()}\"")
    if str(look_direction or "").strip():
        req.append(f"requested look direction = \"{str(look_direction).strip()}\"")
    req_line = ("; ".join(req) + "."
                if req else
                "The view specification does not name an explicit camera zone, but the "
                "camera position is still a core production commitment of this reference.")
    return (
        "CAMERA CORRECTNESS — judge the camera position FIRST, and independently of "
        "anchor visibility:\n"
        f"- {req_line}\n"
        "- Decide where the camera PHYSICALLY IS in the candidate image (which side of "
        "the space it stands on, what it faces), then compare with the request. Do NOT "
        "infer the camera position from the mere presence of required anchors: anchors "
        "being visible does NOT prove the requested camera orientation.\n"
        "- Semantics: front = facing the location's main facade head-on (hero angle); "
        "side = camera beside the subject, facade seen from its flank at an angle; "
        "rear = camera behind the subject, the main facade NOT visible; overhead = a "
        "true top-down/nadir view — no horizon, no sky, roofs and ground planes seen "
        "from directly above (NOT a high 3/4 oblique shot, NOT satellite-stylised "
        "heroes).\n"
        "- Feature-checklist failure pattern (judge view_consistency = fail): a "
        "side/rear/overhead request whose image is actually a frontal, symmetric, "
        "hero-like composition that merely CONTAINS all required anchors. All anchors "
        "visible in one frontal image is evidence AGAINST a side/rear/overhead claim, "
        "not for it.\n"
        "- Report the observed camera position in camera_observed_zone "
        "(front|side|rear|overhead|other|uncertain) and the concrete visual evidence "
        "in camera_evidence (main facade visibility, horizon/sky presence, ground/roof "
        "plane, perspective of vertical edges). camera_observed_zone=uncertain only "
        "when the image genuinely cannot be judged.")


# ============ 确定性推导（§2.2 requested vs observed）============
def derive_camera_view_consistency(requested_zone: str, observed: Optional[str],
                                   model_verdict: Optional[str]) -> Tuple[Optional[str], str]:
    """(view_consistency, derivation_note)。规则：
    - 请求机位无词元命中（None，如 compass 自由串）→ 模型判定原样生效（prompt 已强化）。
    - 模型未报告/报告无效/报告 uncertain → 模型判 pass 封顶为 uncertain（fail closed）。
    - observed 与请求组一致 → 模型判定原样生效。
    - observed 与请求组矛盾（明确的另一具体组）→ fail。
    - observed=other → overhead 请求直接 fail（§2.2：overhead 必须真接近俯视）；
      其余请求 pass 封顶 uncertain（无法核实）。"""
    verdict = model_verdict if model_verdict in ("pass", "concern", "fail") else "uncertain"
    group = camera_zone_group(requested_zone)
    if group is None:
        return verdict, ""
    obs = str(observed or "").strip().lower()
    if obs not in ("front", "side", "rear", "overhead", "other"):
        return (("uncertain" if verdict == "pass" else verdict),
                "camera_observed_zone 缺失/无效/uncertain——无机位证据不得判 pass（fail closed）")
    if obs == group:
        return verdict, ""
    if obs == "other":
        if group == "overhead":
            return "fail", "observed=other：overhead 请求必须真接近正俯视，非俯视机位判 fail"
        return (("uncertain" if verdict == "pass" else verdict),
                f"observed=other，无法核实 requested={group}（fail closed）")
    return "fail", f"observed={obs} 与 requested={group} 矛盾（相机位置错误）"
