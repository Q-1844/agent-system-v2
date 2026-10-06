# src/workflow/templates.py
# Phase 47 C批：W00-W09 参数化工作流模板（PHASE47 §5.1/§5.2）。
#
# 语义边界：
# - 模板 = 一份可复制的工作流定义 + 默认目标（§5.1），不是独立运行引擎；
#   复制产物走与手工搭建完全相同的 PUT definitions 保存/校验路径。
# - 复制产生新工作流身份（新 workflowId），definition.template 记录来源模板与
#   版本（可追溯）；用户可改目标/节点顺序/Skill 绑定/审核次数后再保存。
# - Skill 绑定来自真实目录（full-story-relay-calibration / keyframes-bridge-repair
#   v1.4.0（Phase48 E 收口），binding 名册允许 director）；assembly 不绑 Skill（名册事实）。
# - 模板中的 completion/selection 使用 Phase 47 B批契约（outputs.py/selection.py）。
from __future__ import annotations
from typing import Any, Dict, List, Optional

TEMPLATE_VERSION = "p48_e_templates_v1"

RELAY_SKILL = "full-story-relay-calibration"
BRIDGE_SKILL = "keyframes-bridge-repair"


def _node(nid: str, ntype: str, ref: Optional[str] = None, label: str = "",
          objective: str = "", note: str = "", completion: Optional[dict] = None,
          scope: Optional[dict] = None, instructions: str = "",
          reviewer: str = "", selection: Optional[dict] = None,
          target_artifact: str = "") -> dict:
    cfg: Dict[str, Any] = {}
    if objective:
        cfg["objective"] = objective
    if note:
        cfg["note"] = note
    if completion:
        cfg["completion"] = completion
    if scope:
        cfg["scope"] = scope
    if instructions:
        cfg["instructions"] = instructions
    if reviewer:
        cfg["reviewer"] = reviewer
    if selection:
        cfg["selection"] = selection
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": label or None, "config": cfg,
            "position": {"x": 0, "y": 0}}


def _agent(nid, ref, objective, label="", **kw):
    return _node(nid, "agent", ref, label=label, objective=objective, **kw)


def _review(nid, label, instructions="", reviewer="制作人", selection=None,
            target_artifact="") -> dict:
    return _node(nid, "human_review", label=label, instructions=instructions,
                 reviewer=reviewer, selection=selection,
                 target_artifact=target_artifact)


def _def(name: str, nodes: List[dict], template_id: str, description: str = "",
         notes: str = "", skill_bindings: Optional[List[dict]] = None) -> dict:
    """nodes 依序连成单条执行链；边界节点在两端（纯视觉）。
    skill_bindings = [{skillId, skillVersion, targetNodeId}]——生成真实 Skill 节点
    与 skill_binding 边（编译层唯一认可的绑定路径；§5.1 模板 Skill 来自真实目录）。"""
    core = [n for n in nodes if n["type"] in ("agent", "human_review")]
    edges = []
    seq = ["n_start"] + [n["id"] for n in core] + ["n_end"]
    for i in range(len(seq) - 1):
        edges.append({"id": f"e_{seq[i]}_{seq[i+1]}", "type": "execution",
                      "source": seq[i], "target": seq[i + 1],
                      "sourcePort": "out", "targetPort": "in"})
    all_nodes = ([_node("n_start", "boundary_start"), *nodes,
                  _node("n_end", "boundary_end")])
    for i, sb in enumerate(skill_bindings or []):
        skid = f"n_skill_{i}"
        all_nodes.append(_node(skid, "skill", sb["skillId"],
                               label=sb.get("skillId")))
        edges.append({"id": f"eb_{skid}_{sb['targetNodeId']}",
                      "type": "skill_binding", "source": skid,
                      "target": sb["targetNodeId"], "skillVersion":
                          sb.get("skillVersion") or "1.4.0",
                      "sourcePort": "out", "targetPort": "in"})
    return {"schemaVersion": "workflow_draft_v1",
            "workflowId": None,   # 由创建方填写（新身份）
            "name": name,
            "description": description,
            "notes": notes,
            "status": "draft",
            "nodes": all_nodes,
            "edges": edges,
            "template": {"templateId": template_id,
                         "templateVersion": TEMPLATE_VERSION}}


# ============ W00-W09 模板 ============
def _w00(params: dict) -> dict:
    return _def(
        "W00 生产计划", [
            _agent("n_producer", "producer",
                   params.get("producerObjective") or
                   "拆解制作目标：给出目标、任务范围、依赖与待决定项，形成生产计划。"
                   "只做计划与协调，不委派任何 Specialist。"),
            _review("n_rv1", "计划审定",
                    instructions="检查计划的完整性：目标/范围/依赖/待决定项；"
                                 "确认无越权委派。",
                    target_artifact="生产计划"),
        ], "W00",
        description="Producer 只做目标拆解与协调计划（无写域要求，守卫拦截越权委派）。",
        notes="必需输入：故事目标、已有项目状态、任务范围。")

def _w01(params: dict) -> dict:
    return _def(
        "W01 编剧写作", [
            _agent("n_sw", "script_writer",
                   params.get("writerObjective") or
                   "按创作简报真实新写/修订指定范围的故事（场景段落）。",
                   completion={"strategy": "require_new",
                               "outputs": [{"kind": "scene", "minCount":
                                            int(params.get("sceneCount") or 1)}]}),
            _review("n_rv1", "故事审定", instructions="审读指定范围故事段。",
                    target_artifact="故事版本"),
        ], "W01",
        description="编剧真实新写或修订故事（可在前方插 Producer 计划节点）。",
        notes="必需输入：创作简报、新写或修订范围。修订+合法复用目标可把 completion.strategy "
              "改为 allow_verified_reuse。")

def _w02(params: dict) -> dict:
    look_sel = None
    if params.get("approveLook"):
        look_sel = {"kind": "look_reference", "entityId":
                    params.get("lookId") or "<运行期在审核卡片选择候选>"}
    return _def(
        "W02 角色资源", [
            _agent("n_cd", "character_designer",
                   params.get("characterObjective") or
                   "从文字设定创建角色与 Look，并生成真实参考图候选（不伪造资源；"
                   "缺参考的道具如实列为缺项）。",
                   completion={"strategy": "require_new",
                               "outputs": [
                                   {"kind": "character", "minCount": 1},
                                   {"kind": "look", "withImage": True,
                                    "minCount": 1}]}),
            _review("n_rv1", "角色资源审定", instructions="核对角色身份、Look 图与版本。",
                    selection=look_sel, target_artifact="角色/Look/参考候选"),
        ], "W02",
        description="角色资源生产（角色/Look 图像真实生成）→ 审核；按前置条件可重复角色节点。",
        notes="必需输入：角色、Look、道具目标或已有批准资源。道具无参考图时由 Agent 如实列为缺项。")

def _w03(params: dict) -> dict:
    return _def(
        "W03 场景设计", [
            _agent("n_sc", "scene_designer",
                   params.get("sceneObjective") or
                   "按故事段建立 Location 与 SceneInstance。视图目标按实际需要选择资产类型："
                   "需要表达本故事段具体状态（时段/天气/门已开等）时，用 "
                   "scene.state_view.prepare 生成 SceneInstanceView 状态图；"
                   "仅补地点资料时用 scene.view.prepare 的 LocationView——"
                   "按目标需要生成，不为达标一律生成新图。",
                   completion={"strategy": "require_new",
                               "outputs": [{"kind": "location", "minCount": 1}]}),
            _review("n_rv1", "场景审定", instructions="核对空间事实与环境视图。",
                    target_artifact="Location/SceneInstance/环境视图"),
        ], "W03",
        description="场景设计（Location/SceneInstance + 按需环境视图）→ 审核。",
        notes="必需输入：故事段、地点、时段及空间目标。目标要求具体场景状态时绑定正确 "
              "SceneInstance 与状态版本（SceneInstanceView）；仅要地点资料时不强迫生成状态图。")

def _w04(params: dict) -> dict:
    sel = None
    if params.get("selectFirstFrame"):
        sel = {"kind": "first_frame", "entityId":
               params.get("shotId") or "<运行期在审核卡片选择候选>"}
    return _def(
        "W04 导演分镜准备", [
            _agent("n_dir", "director",
                   params.get("boardObjective") or
                   "形成镜头清单与正式资源绑定，生成分镜图候选。",
                   completion={"strategy": "require_new",
                               "outputs": [{"kind": "shot",
                                            "minCount": int(params.get("shotCount") or 2),
                                            "withImage": True}]}),
            _review("n_rv1", "分镜审定", instructions="核对镜头清单、资源绑定与分镜图候选。",
                    selection=sel, target_artifact="镜头/分镜图候选"),
        ], "W04",
        description="导演分镜准备（镜头清单+资源绑定+分镜图候选）→ 审核（可选首帧选用）。",
        notes="必需输入：故事、资源、场景的具体引用。")

def _w05(params: dict) -> dict:
    bind_skill = bool(params.get("bindRelaySkill", True))
    skill_bindings = []
    nodes = []
    if params.get("includeBoarding"):
        nodes.append(_agent("n_dir_board", "director",
                            "分镜准备：镜头清单与分镜图候选（如上游未完成）。",
                            completion={"strategy": "allow_verified_reuse",
                                        "outputs": [{"kind": "shot", "minCount": 1}]}))
        nodes.append(_review("n_rv0", "分镜审定（首帧可选用）",
                             selection={"kind": "first_frame",
                                        "entityId": params.get("shotId") or
                                        "<运行期在审核卡片选择候选>"}
                             if params.get("selectFirstFrame", True) else None,
                             target_artifact="分镜图候选"))
    nodes.append(_agent(
        "n_dir_video", "director",
        params.get("videoObjective") or
        "为批准首帧的镜头真实生成视频候选：逐镜确定接续方式（正常切镜/重建/接力），"
        "完成后核对首中尾帧。",
        completion={"strategy": "require_new",
                    "outputs": [{"kind": "video_version", "playable": True,
                                 "minCount": int(params.get("videoCount") or 1)}]}))
    if bind_skill:
        skill_bindings.append({"skillId": RELAY_SKILL, "skillVersion": "1.4.0",
                               "targetNodeId": "n_dir_video"})
    nodes.append(_review("n_rv1", "视频审定（选用）",
                         instructions="检查叙事、身份、动作、空间与候选技术状态，"
                                      "并从真实候选中选用用于装配的版本。",
                         selection={"kind": "video_version",
                                    "entityId": params.get("shotId") or
                                    "<运行期在审核卡片选择候选>"},
                         target_artifact="视频候选"))
    return _def("W05 导演视频制作", nodes, "W05",
                description="导演视频生产（绑定尾帧接力校准 Skill）→ 审核（选用视频版本）。",
                notes="必需输入：经批准首帧或合法参考素材、镜头范围。视频完成与用户选片分开。",
                skill_bindings=skill_bindings)

def _w06(params: dict) -> dict:
    # Phase48 A08：修复目标钉定——默认 require_new（修复必须产出新候选，已知问题
    # 视频不得冒充修复结果）；problemVersionId/excludeVersionIds 显式排除问题版本。
    spec = {"kind": "video_version", "playable": True, "minCount": 1}
    exclude: List[str] = []
    if (params.get("problemVersionId") or "").strip():
        exclude.append(params["problemVersionId"].strip())
    if isinstance(params.get("excludeVersionIds"), list):
        exclude += [v for v in params["excludeVersionIds"]
                    if isinstance(v, str) and v.strip()]
    if exclude:
        spec["excludeVersionIds"] = sorted(set(exclude))
    return _def(
        "W06 导演诊断与修复", [
            _agent("n_dir_fix", "director",
                   params.get("fixObjective") or
                   "对给定问题候选先判断第一处偏离（输入资产/根帧/资源绑定/视频过程/"
                   "叙事尾态/剪辑接缝），保留合格部分，做局部处理或小范围重生成；"
                   "交付修复候选与前后对比。原问题版本保留为对照，不得作为修复结果交付。",
                   completion={"strategy": "require_new", "outputs": [spec]}),
            _review("n_rv1", "修复审定", instructions="核对修复候选、前后对比与影响下游清单；"
                                                      "原版保留，未正式切换前下游仍引用原选版。",
                    target_artifact="修复候选+前后对比"),
        ], "W06",
        description="导演诊断与修复（不新增 Repair Agent；依名册绑定已有适用 Skill）。",
        notes="必需输入：已知问题的视频或根帧、反馈与范围。修复目标钉定：completion 默认 "
              "require_new，可用 problemVersionId 排除问题版本本身；确有合格旧产物可复用时"
              "改 strategy=allow_verified_reuse（服务端仍逐项核验），不得以任意旧视频满足修复目标。")

def _w07(params: dict) -> dict:
    bind_skill = bool(params.get("bindBridgeSkill", True))
    skill_bindings = [{"skillId": BRIDGE_SKILL, "skillVersion": "1.4.0",
                       "targetNodeId": "n_dir_bridge"}] if bind_skill else []
    # Phase48 A08：桥接候选同样默认 require_new；excludeVersionIds 可显式排除
    # 不得充当桥接候选的版本（如两端点本身）。
    spec = {"kind": "video_version", "playable": True, "minCount": 1}
    if isinstance(params.get("excludeVersionIds"), list):
        exclude = [v for v in params["excludeVersionIds"]
                   if isinstance(v, str) and v.strip()]
        if exclude:
            spec["excludeVersionIds"] = sorted(set(exclude))
    nodes = [
        _agent("n_dir_bridge", "director",
               params.get("bridgeObjective") or
               "两个端点各自合格且必须连续过渡时按 keyframes 桥接方法插桥：双端证据、"
               "桥时长、中间节拍；正常切镜判为不需要桥接，无效端点先修端点。",
               completion={"strategy": "require_new", "outputs": [spec]}),
        _review("n_rv1", "桥接审定", instructions="核对桥接候选、双端请求依据与拼接预览；"
                                                  "是否加入 Sequence 由用户决定。",
                target_artifact="桥接候选+拼接预览"),
    ]
    return _def("W07 有效端点桥接", nodes, "W07",
                description="导演有效端点桥接（绑定 keyframes 接续桥 Skill）→ 审核。",
                notes="必需输入：两个合格且拟保留的端点和真实接缝问题。桥接候选默认要求本节点"
                      "新产出（require_new）；excludeVersionIds 可排除端点版本冒充桥接候选。",
                skill_bindings=skill_bindings)

def _w08(params: dict) -> dict:
    vv_scope = params.get("videoVersions")   # [{shotId, versionId}, ...] 显式声明
    scope = {"videoVersions": vv_scope} if vv_scope else None
    nodes = []
    if not vv_scope:
        # 无显式声明时：装配前的确认审核承担「从候选中正式选用」职责
        nodes.append(_review("n_rv0", "装配素材确认（选用视频版本）",
                             instructions="从既有视频候选中正式选用用于本片装配的版本。",
                             selection={"kind": "video_version",
                                        "entityId": params.get("shotId") or
                                        "<运行期在审核卡片选择候选>"},
                             target_artifact="视频候选"))
    nodes.append(_agent("n_asm", "assembly",
                        params.get("assemblyObjective") or
                        "按选片范围装配 Sequence：默认保留合格原生环境声与动作声"
                        "（逐镜先分类再决定：保留/控增益/如实标记不可用），"
                        "禁止自动旁白；音乐仅按作品政策执行。完成渲染并核验可播放。",
                        scope=scope,
                        completion={"strategy": "require_new",
                                    "outputs": [{"kind": "sequence",
                                                 "withCompletedRender": True,
                                                 "minCount": 1}]}))
    nodes.append(_review("n_rv1", "成片审定（可选 render 选用）",
                         selection={"kind": "sequence_render",
                                    "entityId": params.get("sequenceId") or
                                    "<运行期在审核卡片选择候选>"}
                         if params.get("selectRender") else None,
                         target_artifact="Sequence/成片候选"))
    return _def("W08 现有视频装配", nodes, "W08",
                description="装配既有已选视频（合法复用；真实 Sequence + 原生音轨政策 + render）→ 审核。",
                notes="必需输入：已选视频版本、音频政策（禁止自动旁白/默认保留合格原生音效/"
                      "音乐按作品要求）。显式复用走 scope.videoVersions。")

def _w09(params: dict) -> dict:
    """六员完整生产：默认链（§5.9）；节点实例可按前置条件细分。"""
    relay_skill = bool(params.get("bindRelaySkill", True))
    skill_bindings = [{"skillId": RELAY_SKILL, "skillVersion": "1.4.0",
                       "targetNodeId": "n_dir_video"}] if relay_skill else []
    video_node = _agent(
        "n_dir_video", "director",
        params.get("videoObjective") or
        "按批准首帧与镜头清单真实生成全部视频候选：逐镜决定接续方式，"
        "完成后核对首中尾帧与剧情尾态。",
        completion={"strategy": "require_new",
                    "outputs": [{"kind": "video_version", "playable": True,
                                 "minCount": int(params.get("videoCount") or 6)}]})
    nodes = [
        _agent("n_producer", "producer",
               params.get("producerObjective") or
               "拆解生产目标为制作计划（不委派 Specialist）。"),
        _agent("n_sw", "script_writer",
               params.get("writerObjective") or "按作品简报真实新写全片故事段。",
               completion={"strategy": "require_new",
                           "outputs": [{"kind": "scene", "minCount": 3}]}),
        _review("n_rv_sw", "故事审定", target_artifact="故事版本"),
        _agent("n_cd", "character_designer",
               params.get("characterObjective") or
               "生产角色、Look 与必要道具参考（缺参考道具如实列为缺项）。",
               completion={"strategy": "require_new",
                           "outputs": [{"kind": "character", "minCount": 1},
                                       {"kind": "look", "withImage": True,
                                        "minCount": 1}]}),
        _review("n_rv_cd", "资源审定（批准 Look）",
                selection={"kind": "look_reference", "entityId":
                           "<运行期在审核卡片选择候选>"},
                target_artifact="角色/Look/参考候选"),
        _agent("n_sc", "scene_designer",
               params.get("sceneObjective") or
               "建立全部场景 Location/SceneInstance；每个故事段涉及的具体状态"
               "（时段/天气/门已开等）用 scene.state_view.prepare 生成 "
               "SceneInstanceView 状态图并绑定正确 SceneInstance——后续分镜的"
               "首帧与视频以状态图为空间依据；仅补地点资料时才用 LocationView。",
               completion={"strategy": "require_new",
                           "outputs": [{"kind": "location", "minCount": 2}]}),
        _review("n_rv_sc", "场景审定", target_artifact="场景与视图"),
        _agent("n_dir_board", "director",
               params.get("boardObjective") or
               "形成镜头清单、资源绑定与分镜图候选（≥6 镜，含同地点连续动作、"
               "一次换场与一次中近景身份检查——由你按故事判断）。",
               completion={"strategy": "require_new",
                           "outputs": [{"kind": "shot", "withImage": True,
                                        "minCount": 6}]}),
        _review("n_rv_board", "分镜审定（首帧选用）",
                selection={"kind": "first_frame", "entityId":
                           "<运行期在审核卡片选择候选>"},
                target_artifact="分镜图候选"),
        video_node,
        _review("n_rv_v1", "视频一审",
                instructions="第一轮独立审核：叙事、身份、动作、空间与候选技术状态。",
                target_artifact="视频候选"),
        _review("n_rv_v2", "视频二审（选用装配版本）",
                instructions="第二轮独立审核：保留一审意见，完成用于装配的具体版本选用。",
                selection={"kind": "video_version", "entityId":
                           "<运行期在审核卡片选择候选>"},
                target_artifact="视频候选"),
        _agent("n_asm", "assembly",
               params.get("assemblyObjective") or
               "接收精确选片范围装配成片：禁止自动旁白；默认保留合格原生环境声与动作声"
               "（逐镜先分类再决定：保留/控增益/如实标记不可用，不合格片段给修复选项"
               "交政策裁定）；音乐仅按作品政策。完成渲染并核验可播放。",
               completion={"strategy": "require_new",
                           "outputs": [{"kind": "sequence",
                                        "withCompletedRender": True,
                                        "minCount": 1}]}),
        _review("n_rv_final", "成片审定", target_artifact="可审成片"),
    ]
    return _def("W09 六员完整生产", nodes, "W09",
                description="六员完整生产及独立双审核：新故事 → 可审成片的完整证据链。",
                notes="必需输入：新故事目标、本轮合法资产审批。桥接 Skill 仅在真实接缝需要时使用。",
                skill_bindings=skill_bindings)


TEMPLATES: Dict[str, dict] = {
    "W00": {"id": "W00", "name": "W00 生产计划",
            "description": "Producer 只做目标拆解与协调计划 → 审核。",
            "requiredInputs": ["故事目标", "已有项目状态", "任务范围"],
            "expectedOutputs": ["持久化计划与待决定项；无隐藏专业委派"],
            "build": _w00,
            "params": [{"key": "producerObjective", "label": "Producer 节点目标",
                        "required": False}]},
    "W01": {"id": "W01", "name": "W01 编剧写作",
            "description": "编剧真实新写/修订故事 → 审核（可在前方插 Producer）。",
            "requiredInputs": ["创作简报", "新写或修订范围"],
            "expectedOutputs": ["实际故事段或正文版本及回执"],
            "build": _w01,
            "params": [{"key": "writerObjective", "label": "编剧节点目标",
                        "required": False},
                       {"key": "sceneCount", "label": "最少场景数", "required": False}]},
    "W02": {"id": "W02", "name": "W02 角色资源",
            "description": "角色/Look 图像真实生成 → 审核（可批准 Look 参考）。",
            "requiredInputs": ["角色、Look、道具目标或已有批准资源"],
            "expectedOutputs": ["新参考图候选、明确版本、合法复用记录"],
            "build": _w02,
            "params": [{"key": "characterObjective", "label": "角色节点目标",
                        "required": False},
                       {"key": "approveLook", "label": "审核时批准 Look 参考",
                        "required": False},
                       {"key": "lookId", "label": "Look ID（approveLook 时）",
                        "required": False}]},
    "W03": {"id": "W03", "name": "W03 场景设计",
            "description": "Location/SceneInstance + 环境视图 → 审核。",
            "requiredInputs": ["故事段", "地点", "时段及空间目标"],
            "expectedOutputs": ["Location 与 SceneInstance、实际环境视图"],
            "build": _w03,
            "params": [{"key": "sceneObjective", "label": "场景节点目标",
                        "required": False}]},
    "W04": {"id": "W04", "name": "W04 导演分镜准备",
            "description": "镜头清单+资源绑定+分镜图候选 → 审核（可选首帧选用）。",
            "requiredInputs": ["故事", "资源", "场景的具体引用"],
            "expectedOutputs": ["镜头清单、资源绑定、真实分镜图候选"],
            "build": _w04,
            "params": [{"key": "boardObjective", "label": "分镜节点目标",
                        "required": False},
                       {"key": "shotCount", "label": "最少镜头数", "required": False},
                       {"key": "selectFirstFrame", "label": "审核时选用首帧",
                        "required": False},
                       {"key": "shotId", "label": "镜头 ID（选首帧时）",
                        "required": False}]},
    "W05": {"id": "W05", "name": "W05 导演视频制作",
            "description": "导演视频生产（尾帧接力 Skill）→ 审核（选用视频版本）。",
            "requiredInputs": ["经批准首帧或合法参考素材", "镜头范围"],
            "expectedOutputs": ["真视频版本、生成依据、首中尾帧与候选清单"],
            "build": _w05,
            "params": [{"key": "videoObjective", "label": "视频节点目标",
                        "required": False},
                       {"key": "bindRelaySkill", "label": "绑定尾帧接力校准 Skill",
                        "required": False},
                       {"key": "includeBoarding", "label": "前置分镜准备节点",
                        "required": False},
                       {"key": "selectFirstFrame", "label": "分镜审核时选用首帧",
                        "required": False},
                       {"key": "videoCount", "label": "最少视频数", "required": False},
                       {"key": "shotId", "label": "镜头 ID", "required": False}]},
    "W06": {"id": "W06", "name": "W06 导演诊断与修复",
            "description": "诊断第一处偏离 → 修复候选+前后对比 → 审核。",
            "requiredInputs": ["已知问题的视频或根帧", "反馈与范围"],
            "expectedOutputs": ["修复候选、前后对比、影响下游清单"],
            "build": _w06,
            "params": [{"key": "fixObjective", "label": "修复节点目标",
                        "required": False},
                       {"key": "problemVersionId", "label": "问题版本 ID"
                        "（排除其冒充修复结果）", "required": False},
                       {"key": "excludeVersionIds", "label": "排除版本 ID 清单 JSON 数组",
                        "required": False}]},
    "W07": {"id": "W07", "name": "W07 有效端点桥接",
            "description": "双端合格的接缝桥接（keyframes 接续桥 Skill）→ 审核。",
            "requiredInputs": ["两个合格且拟保留的端点", "真实接缝问题"],
            "expectedOutputs": ["桥接候选、双端请求依据与拼接预览"],
            "build": _w07,
            "params": [{"key": "bridgeObjective", "label": "桥接节点目标",
                        "required": False},
                       {"key": "bindBridgeSkill", "label": "绑定接续桥 Skill",
                        "required": False},
                       {"key": "excludeVersionIds", "label": "排除版本 ID 清单 JSON 数组",
                        "required": False}]},
    "W08": {"id": "W08", "name": "W08 现有视频装配",
            "description": "装配已选视频（真实 Sequence/原生音轨政策/render）→ 审核。",
            "requiredInputs": ["已选视频版本", "音频政策（禁止自动旁白/默认保留合格原生音效/音乐按作品要求）"],
            "expectedOutputs": ["Sequence、音轨（原生保留或按政策处理）、完成 render"],
            "build": _w08,
            "params": [{"key": "assemblyObjective", "label": "装配节点目标",
                        "required": False},
                       {"key": "videoVersions", "label": "显式视频版本清单 JSON"
                        "（[{shotId,versionId}]）", "required": False},
                       {"key": "shotId", "label": "镜头 ID（审核选用时）",
                        "required": False},
                       {"key": "selectRender", "label": "成片审核时选用 render",
                        "required": False},
                       {"key": "sequenceId", "label": "Sequence ID（选用 render 时）",
                        "required": False}]},
    "W09": {"id": "W09", "name": "W09 六员完整生产",
            "description": "六员完整生产及独立双审核：新故事 → 可审成片。",
            "requiredInputs": ["新故事目标", "本轮合法资产审批"],
            "expectedOutputs": ["从新故事到可审成片的完整证据链"],
            "build": _w09,
            "params": [{"key": "producerObjective", "label": "Producer 目标",
                        "required": False},
                       {"key": "writerObjective", "label": "编剧目标", "required": False},
                       {"key": "characterObjective", "label": "角色目标", "required": False},
                       {"key": "sceneObjective", "label": "场景目标", "required": False},
                       {"key": "boardObjective", "label": "分镜目标", "required": False},
                       {"key": "videoObjective", "label": "视频目标", "required": False},
                       {"key": "assemblyObjective", "label": "装配目标", "required": False},
                       {"key": "videoCount", "label": "最少视频数", "required": False},
                       {"key": "bindRelaySkill", "label": "绑定尾帧接力校准 Skill",
                        "required": False}]},
}


def list_templates() -> list:
    out = []
    for tid, t in TEMPLATES.items():
        out.append({"id": t["id"], "name": t["name"],
                    "description": t["description"],
                    "requiredInputs": t["requiredInputs"],
                    "expectedOutputs": t["expectedOutputs"],
                    "params": t["params"],
                    "templateVersion": TEMPLATE_VERSION})
    return out


def create_from_template(template_id: str, workflow_id: str,
                         params: Optional[dict] = None) -> dict:
    """从模板构建定义（复制产生新身份；调用方随后走保存/校验路径）。"""
    t = TEMPLATES.get(template_id)
    if t is None:
        raise KeyError(f"未知模板: {template_id}")
    d = t["build"](params or {})
    d["workflowId"] = workflow_id
    d["template"] = {"templateId": t["id"], "templateVersion": TEMPLATE_VERSION}
    return d
