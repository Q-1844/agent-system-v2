# routers/shot_binding.py
# Shot Production Binding HTTP 层（S3B.4）。
#
# 命名空间 /api/project/{project}/shot-bindings/*。
# 🔴 业务全部在 src/shot_binding/service.py（project_write_lock / per-binding OCC /
#    fail-closed 校验 / 指纹快照 / validity read model / refresh 均已实现并有测试）；
#    本文件只做 HTTP mapping——receipt ok=False → 对应状态码（body 保持完整 receipt
#    结构，与 scene_system._raise_for_receipt 同口径），绝不复制业务逻辑。
#
# 端点：
# - GET    /                                          document read（全部 bindings；分镜卡角标用）
# - GET    /{shot_id}                                 单条 binding read（无条目 → binding: null，非错误）
# - GET    /{shot_id}/validity                        Derived Validity（READY / NOT_READY / UNBOUND + issues）
# - POST   /                                          create（payload 含 shot_id/scene_id/...；锁内 absence check 即 OCC）
# - PUT    /{shot_id}                                 replace（body 顶层含 expected_binding_revision）
# - DELETE /{shot_id}?expected_binding_revision=      delete
# - POST   /{shot_id}/refresh                         显式 refresh（唯一更新指纹快照的操作）
#
# 注意：BOUND shot 的生成走 Binding-aware 路径（READY→Pack→Adapter；NOT_READY→
# 结构化拒绝，绝不 fallback legacy，见 src/generation/image_adapter._gate_ready_pack）
# ——绑定 / 解绑是生产行为变更，前端必须展示 validity。
from fastapi import APIRouter, HTTPException, Query
from deps import get_project_manager
from src.shot_binding.models import ShotBindingSchemaError
from src.shot_binding.service import ShotBindingService

router = APIRouter()

# receipt error 码 → HTTP status（只做 mapping；语义收口在 service）
_ERROR_STATUS = {
    # ---- 400：入参 / 语义校验（fail closed）----
    "validation_error": 400,
    "invalid_binding": 400,
    "invalid_relation": 400,
    "invalid_anchor": 400,
    "invalid_action_kind": 400,
    "action_prop_required": 400,
    "action_subject_required": 400,
    "action_subject_unknown": 400,
    "action_prop_missing": 404,
    "duplicate_character_binding": 400,
    "duplicate_prop_binding": 400,
    "cast_mismatch": 400,
    "scene_mismatch": 400,
    "story_scene_mismatch": 400,
    "binding_conflict": 400,
    "look_character_mismatch": 400,
    "composition_character_mismatch": 400,
    "composition_look_mismatch": 400,
    "composition_not_approved": 400,
    "look_not_approved": 400,
    "prop_not_approved": 400,
    "scene_instance_not_active": 400,
    "scene_instance_view_not_approved": 400,
    "scene_instance_view_instance_mismatch": 400,
    "scene_instance_view_view_mismatch": 400,
    "project_visual_profile_unspecified": 400,
    # ---- 404：实体不存在 ----
    "shot_missing": 404,
    "character_missing": 404,
    "look_missing": 404,
    "prop_missing": 404,
    "composition_missing": 404,
    "scene_instance_missing": 404,
    "location_view_missing": 404,
    "scene_instance_view_missing": 404,
    "binding_not_found": 404,
    # ---- 409：状态冲突 / OCC ----
    "binding_already_exists": 409,
    "stale_binding_revision": 409,
    "orphan_binding": 409,
    # ---- 500：canonical 不可读 ----
    "project_unreadable": 500,
    "resources_unreadable": 500,
    "scene_system_unreadable": 500,
    "visual_profile_unreadable": 500,
}


def _service(project: str) -> ShotBindingService:
    return ShotBindingService(get_project_manager().get_project_dir(project), project)


def _raise_for_receipt(receipt: dict):
    """业务 receipt（ok=False）→ HTTP 状态码；body 保持完整 receipt 结构。"""
    if receipt.get("ok"):
        return
    code = receipt.get("error") or "internal_error"
    raise HTTPException(status_code=_ERROR_STATUS.get(code, 500), detail=receipt)


def _project_dir_or_404(project: str):
    try:
        return get_project_manager().get_project_dir(project)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}") from e


def _guard_schema(fn):
    """ShotBindingSchemaError（canonical 损坏/未知 schema）→ 500，不伪装成业务 NOT_READY。"""
    try:
        return fn()
    except ShotBindingSchemaError as e:
        raise HTTPException(status_code=500, detail=f"shot_production_bindings.json 数据错误: {e}")


# ============ 读 ============
@router.get("/api/project/{project}/shot-bindings")
async def read_bindings_document(project: str):
    pdir = _project_dir_or_404(project)
    return _guard_schema(lambda: _service(project).read_document())


@router.get("/api/project/{project}/shot-bindings/{shot_id}/validity")
async def read_binding_validity(project: str, shot_id: str):
    pdir = _project_dir_or_404(project)
    return _guard_schema(lambda: _service(project).read_validity(shot_id))


@router.get("/api/project/{project}/shot-bindings/{shot_id}")
async def read_shot_binding(project: str, shot_id: str):
    pdir = _project_dir_or_404(project)
    r = _guard_schema(lambda: _service(project).read_binding(shot_id))
    if r is None:
        # UNBOUND 不是错误（§十四）：无条目 → null（零副作用，绝不自动创建）
        return {"binding": None, "binding_revision": None}
    return r


# ============ 写（async 包装持 project_write_lock）============
@router.post("/api/project/{project}/shot-bindings")
async def create_shot_binding(project: str, payload: dict):
    receipt = await _service(project).create_binding_async(payload or {})
    _raise_for_receipt(receipt)
    return receipt


@router.put("/api/project/{project}/shot-bindings/{shot_id}")
async def replace_shot_binding(project: str, shot_id: str, payload: dict):
    body = dict(payload or {})
    expected = str(body.pop("expected_binding_revision", "") or "")
    receipt = await _service(project).replace_binding_async(
        shot_id, body, expected_binding_revision=expected)
    _raise_for_receipt(receipt)
    return receipt


@router.delete("/api/project/{project}/shot-bindings/{shot_id}")
async def delete_shot_binding(project: str, shot_id: str,
                              expected_binding_revision: str = Query(default="")):
    receipt = await _service(project).delete_binding_async(
        shot_id, expected_binding_revision=expected_binding_revision)
    _raise_for_receipt(receipt)
    return receipt


@router.post("/api/project/{project}/shot-bindings/{shot_id}/refresh")
async def refresh_shot_binding(project: str, shot_id: str, body: dict):
    expected = str((body or {}).get("expected_binding_revision", "") or "")
    receipt = await _service(project).refresh_binding_async(
        shot_id, expected_binding_revision=expected)
    _raise_for_receipt(receipt)
    return receipt
