"""Scene System V1 路由（Phase S1 Foundation + Phase S2A 视觉协议）。

命名空间 /api/project/{project}/scene-system/*：
🔴 不与现有 /scenes（Story Scene 对账保存）、/story、/resources、/character-design
   路径空间冲突。Scene System ≠ 现有 Story Scene（story_scene_id 单向引用，见
   src/scene_system/models.py 头部审计结论）。

Phase S2A 视觉协议端点（业务在 src/scene_system/visual_service.py / view_review.py）：
- POST /scene-system/views/{view_id}/generate   三阶段候选生成（provider 在锁外）
- POST /scene-system/views/{view_id}/review     六维度 Vision Review（canonical 零写入）
- POST /scene-system/views/{view_id}/approve    显式 Approval（cross-canonical saga）
- POST /scene-system/views/{view_id}/revoke     解除 approved（历史保留）
- GET  /scene-system/views/{view_id}/generations|reviews|approvals   证据流只读
- POST /scene-system/recovery/run               孤儿 generating 清理（幂等；startup 已挂载）
🔴 仍不接任何 Agent；LocationView 生成 = Base Location identity，不做 SceneState 状态图（§29）。

Phase S1 读 API（GET，sync service、零副作用——绝不创建 scene_system.json）：
- GET  /api/project/{project}/scene-system/overview
        文档概览 + document_revision（创建类 mutation 的 OCC 期望值来源）
- GET  /api/project/{project}/scene-system/locations            Location 列表（含派生 anchor/view/layout 索引）
- GET  /api/project/{project}/scene-system/locations/{location_id}
- GET  /api/project/{project}/scene-system/anchors              ?location_id=
- GET  /api/project/{project}/scene-system/anchors/{anchor_id}
- GET  /api/project/{project}/scene-system/layouts/{layout_id}
- GET  /api/project/{project}/scene-system/locations/{location_id}/layout   （按 location 查 1:0..1）
- GET  /api/project/{project}/scene-system/views                ?location_id=
- GET  /api/project/{project}/scene-system/views/{view_id}
- GET  /api/project/{project}/scene-system/scene-instances      ?story_scene_id=&location_id=（含 story_scene_missing 标注）
- GET  /api/project/{project}/scene-system/scene-instances/{instance_id}

写 API（lock/OCC/persistence 全在 service；每个 mutation 必须带 OCC 期望值）：
- POST   /api/project/{project}/scene-system/locations                      （expected_document_revision）
- PATCH  /api/project/{project}/scene-system/locations/{location_id}        （expected_revision；rename=改 name）
- DELETE /api/project/{project}/scene-system/locations/{location_id}        （expected_revision；有从属→409 location_in_use）
- POST   /api/project/{project}/scene-system/anchors                        （expected_document_revision）
- PATCH  /api/project/{project}/scene-system/anchors/{anchor_id}            （expected_revision；relations 整组替换）
- DELETE /api/project/{project}/scene-system/anchors/{anchor_id}            （expected_revision；被引用→409 anchor_in_use）
- POST   /api/project/{project}/scene-system/locations/{location_id}/layout （expected_document_revision；1:0..1）
- PATCH  /api/project/{project}/scene-system/layouts/{layout_id}            （expected_revision）
- DELETE /api/project/{project}/scene-system/layouts/{layout_id}            （expected_revision）
- POST   /api/project/{project}/scene-system/views                          （expected_document_revision）
- PATCH  /api/project/{project}/scene-system/views/{view_id}                （expected_revision）
- DELETE /api/project/{project}/scene-system/views/{view_id}                （expected_revision）
- POST   /api/project/{project}/scene-system/scene-instances                （expected_document_revision；T11/T12/T13 校验）
- PATCH  /api/project/{project}/scene-system/scene-instances/{instance_id}/location
                                                                             （expected_revision；换绑 fail closed）
- DELETE /api/project/{project}/scene-system/scene-instances/{instance_id}   （expected_revision）
- PATCH  /api/project/{project}/scene-system/scene-instances/{instance_id}/state
                                                                             （expected_revision；SceneState patch）

🔴 router 只做 request validation / HTTP mapping；业务逻辑全在 service 层。
🔴 不接 Agent 工具（S1/S2A 两轮一致）。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

import deps
from deps import get_project_manager
from src.scene_system.service import SceneSystemService
from src.scene_system.models import SceneSystemSchemaError

router = APIRouter()


def _service(project_name: str) -> SceneSystemService:
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_name}")
    return SceneSystemService(project_dir, project_name)


def _visual_service(project_name: str):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_name}")
    from src.scene_system.visual_service import SceneVisualService
    return SceneVisualService(project_dir, project_name)


# receipt error 码 → HTTP status（router 只做 HTTP mapping）
_ERROR_STATUS = {
    "stale_revision": 409,
    "not_found": 404,
    "location_not_found": 404,
    "anchor_not_found": 404,
    "layout_not_found": 404,
    "view_not_found": 404,
    "instance_not_found": 404,
    "story_scene_not_found": 404,
    "prop_not_found": 404,
    "location_in_use": 409,
    "anchor_in_use": 409,
    "instance_already_active": 409,
    "layout_already_exists": 409,
    "state_references_old_location": 409,
    "validation_error": 400,
    "invalid_relation": 400,
    "cross_location_relation": 422,
    "cross_location_anchor": 422,
    "resources_lookup_failed": 500,
    # Project Visual Profile P1：unspecified fail closed / 坏文件
    "project_visual_profile_required": 422,
    "visual_profile_unreadable": 500,
    # ---- Phase S2A 视觉协议 ----
    "generation_in_progress": 409,
    "stale_generation_basis": 409,
    "stale_approval_basis": 409,
    "invalid_candidate_count": 400,
    "invalid_candidate_index": 400,
    "no_candidates": 409,
    "not_ready": 409,
    "generation_record_missing": 500,
    "candidate_file_missing": 500,
    "reference_view_not_approved": 400,
    "reference_not_usable": 400,
    "reference_budget_exceeded": 400,
    "review_pass_required": 409,
    "already_approved": 409,
    "not_approved": 409,
    "resources_stale_revision": 409,
    "resources_register_failed": 502,
    # ---- Phase S2B SceneInstanceView ----
    "instance_view_not_found": 404,
    "scene_instance_not_found": 404,
    "location_mismatch": 422,
    "scene_instance_view_already_exists": 409,
    "location_view_stale": 409,
    "base_reference_missing": 409,
    "canonical_changed": 500,
    "location_view_not_approved": 409,
    "prop_missing": 409,
}


def _raise_for_receipt(receipt: dict):
    """业务 receipt（ok=False）→ 对应 HTTP 状态码；body 保持完整 receipt 结构。"""
    if receipt.get("ok"):
        return
    code = receipt.get("error") or "internal_error"
    status = _ERROR_STATUS.get(code, 500)
    raise HTTPException(status_code=status, detail=receipt)


# ============ 读 API ============
@router.get("/api/project/{project}/scene-system/overview")
async def get_overview(project: str):
    try:
        return _service(project).document_overview()
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/locations")
async def list_locations(project: str):
    try:
        return {"locations": _service(project).list_locations(),
                "document_revision": _service(project).document_overview()["document_revision"]}
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/locations/{location_id}")
async def read_location(project: str, location_id: str):
    try:
        d = _service(project).read_location(location_id)
        if d is None:
            raise HTTPException(status_code=404, detail=f"Location 不存在: {location_id}")
        return d
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/anchors")
async def list_anchors(project: str, location_id: Optional[str] = Query(default=None)):
    try:
        return {"anchors": _service(project).list_anchors(location_id)}
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/anchors/{anchor_id}")
async def read_anchor(project: str, anchor_id: str):
    try:
        d = _service(project).read_anchor(anchor_id)
        if d is None:
            raise HTTPException(status_code=404, detail=f"Anchor 不存在: {anchor_id}")
        return d
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/layouts/{layout_id}")
async def read_layout(project: str, layout_id: str):
    try:
        d = _service(project).read_layout(layout_id)
        if d is None:
            raise HTTPException(status_code=404, detail=f"SetLayout 不存在: {layout_id}")
        return d
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/locations/{location_id}/layout")
async def read_layout_by_location(project: str, location_id: str):
    try:
        d = _service(project).read_layout_by_location(location_id)
        if d is None:
            raise HTTPException(status_code=404,
                                detail=f"该 Location 无 SetLayout: {location_id}")
        return d
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/views")
async def list_views(project: str, location_id: Optional[str] = Query(default=None)):
    try:
        return {"views": _service(project).list_views(location_id)}
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/views/{view_id}")
async def read_view(project: str, view_id: str):
    try:
        d = _service(project).read_view(view_id)
        if d is None:
            raise HTTPException(status_code=404, detail=f"LocationView 不存在: {view_id}")
        return d
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/scene-instances")
async def list_scene_instances(project: str,
                               story_scene_id: Optional[str] = Query(default=None),
                               location_id: Optional[str] = Query(default=None)):
    try:
        return {"scene_instances": _service(project).list_scene_instances(
            story_scene_id=story_scene_id, location_id=location_id)}
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/scene-instances/{instance_id}")
async def read_scene_instance(project: str, instance_id: str):
    try:
        d = _service(project).read_scene_instance(instance_id)
        if d is None:
            raise HTTPException(status_code=404, detail=f"SceneInstance 不存在: {instance_id}")
        return d
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 写 API：Location ============
class CreateLocationRequest(BaseModel):
    name: str
    description: str = ""
    identity_description: str = ""
    style_notes: str = ""
    expected_document_revision: str


@router.post("/api/project/{project}/scene-system/locations")
async def create_location(project: str, req: CreateLocationRequest):
    try:
        receipt = await _service(project).create_location(
            name=req.name, description=req.description,
            identity_description=req.identity_description, style_notes=req.style_notes,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/scene-system/locations/{location_id}")
async def update_location(project: str, location_id: str, patch: dict = Body(...)):
    try:
        body = {k: v for k, v in patch.items() if k != "expected_revision"}
        receipt = await _service(project).update_location(
            location_id, body, expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/scene-system/locations/{location_id}")
async def delete_location(project: str, location_id: str,
                          expected_revision: str = Query(...)):
    try:
        receipt = await _service(project).delete_location(
            location_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 写 API：Anchor ============
class CreateAnchorRequest(BaseModel):
    location_id: str
    name: str
    kind: str = "other"
    description: str = ""
    semantic_zone: str = ""
    relations: Optional[List[Dict[str, Any]]] = None
    expected_document_revision: str


@router.post("/api/project/{project}/scene-system/anchors")
async def create_anchor(project: str, req: CreateAnchorRequest):
    try:
        receipt = await _service(project).create_anchor(
            location_id=req.location_id, name=req.name, kind=req.kind,
            description=req.description, semantic_zone=req.semantic_zone,
            relations=req.relations,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/scene-system/anchors/{anchor_id}")
async def update_anchor(project: str, anchor_id: str, patch: dict = Body(...)):
    try:
        body = {k: v for k, v in patch.items() if k != "expected_revision"}
        receipt = await _service(project).update_anchor(
            anchor_id, body, expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/scene-system/anchors/{anchor_id}")
async def delete_anchor(project: str, anchor_id: str,
                        expected_revision: str = Query(...)):
    try:
        receipt = await _service(project).delete_anchor(
            anchor_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 写 API：SetLayout ============
class CreateLayoutRequest(BaseModel):
    orientation_convention: str = ""
    notes: str = ""
    placements: Optional[List[Dict[str, Any]]] = None
    reference_asset_id: Optional[str] = None
    expected_document_revision: str


@router.post("/api/project/{project}/scene-system/locations/{location_id}/layout")
async def create_layout(project: str, location_id: str, req: CreateLayoutRequest):
    try:
        receipt = await _service(project).create_layout(
            location_id=location_id, orientation_convention=req.orientation_convention,
            notes=req.notes, placements=req.placements,
            reference_asset_id=req.reference_asset_id,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/scene-system/layouts/{layout_id}")
async def update_layout(project: str, layout_id: str, patch: dict = Body(...)):
    try:
        body = {k: v for k, v in patch.items() if k != "expected_revision"}
        receipt = await _service(project).update_layout(
            layout_id, body, expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/scene-system/layouts/{layout_id}")
async def delete_layout(project: str, layout_id: str,
                        expected_revision: str = Query(...)):
    try:
        receipt = await _service(project).delete_layout(
            layout_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 写 API：LocationView ============
class CreateViewRequest(BaseModel):
    location_id: str
    label: str = ""
    camera_zone: str = ""
    look_direction: str = ""
    visible_anchor_ids: Optional[List[str]] = None
    reference_asset_id: Optional[str] = None
    expected_document_revision: str


@router.post("/api/project/{project}/scene-system/views")
async def create_view(project: str, req: CreateViewRequest):
    try:
        receipt = await _service(project).create_view(
            location_id=req.location_id, label=req.label,
            camera_zone=req.camera_zone, look_direction=req.look_direction,
            visible_anchor_ids=req.visible_anchor_ids,
            reference_asset_id=req.reference_asset_id,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/scene-system/views/{view_id}")
async def update_view(project: str, view_id: str, patch: dict = Body(...)):
    try:
        body = {k: v for k, v in patch.items() if k != "expected_revision"}
        receipt = await _service(project).update_view(
            view_id, body, expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/scene-system/views/{view_id}")
async def delete_view(project: str, view_id: str,
                      expected_revision: str = Query(...)):
    try:
        receipt = await _service(project).delete_view(
            view_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 写 API：SceneInstance / SceneState ============
class CreateSceneInstanceRequest(BaseModel):
    story_scene_id: str
    location_id: str
    expected_document_revision: str


@router.post("/api/project/{project}/scene-system/scene-instances")
async def create_scene_instance(project: str, req: CreateSceneInstanceRequest):
    try:
        receipt = await _service(project).create_scene_instance(
            story_scene_id=req.story_scene_id, location_id=req.location_id,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/scene-system/scene-instances/{instance_id}")
async def delete_scene_instance(project: str, instance_id: str, expected_revision: str = Query(...)):
    """删除 SceneInstance（连同嵌入 SceneState；Phase D §35：服务层既有能力补齐 HTTP 面，
    与 locations/anchors/layouts/views 的 delete 对齐）。OCC：expected_revision 必传。"""
    try:
        receipt = await _service(project).delete_scene_instance(
            instance_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/scene-system/scene-instances/{instance_id}/location")
async def update_scene_instance_location(project: str, instance_id: str,
                                         patch: dict = Body(...)):
    """换绑 Location（fail closed：state 引用旧 location anchor → 409）。"""
    try:
        receipt = await _service(project).update_scene_instance_location(
            instance_id, location_id=patch.get("location_id", ""),
            expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/scene-system/scene-instances/{instance_id}")
async def delete_scene_instance(project: str, instance_id: str,
                                expected_revision: str = Query(...)):
    try:
        receipt = await _service(project).delete_scene_instance(
            instance_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/scene-system/scene-instances/{instance_id}/state")
async def patch_scene_state(project: str, instance_id: str, patch: dict = Body(...)):
    """SceneState patch。语义（S2A §6E，与 service 一致）：
    - 字符串字段（time_of_day/weather/lighting/environment_conditions/notes）：partial——
      未传字段保持不变；
    - anchor_states / prop_placements：**整 dict 替换**——未传字段保持不变，传了则整个
      dict 按传入内容替换（不是 per-key merge）；并发安全由 expected_revision (OCC) 保障，
      调用方 read-patch-write。"""
    try:
        body = {k: v for k, v in patch.items() if k != "expected_revision"}
        receipt = await _service(project).patch_scene_state(
            instance_id, body, expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ Phase S2A：LocationView 视觉协议（generate/review/approve/revoke）============
class GenerateLocationViewRequest(BaseModel):
    expected_revision: str
    candidate_count: int = 2
    seed: Optional[int] = None
    reference_view_ids: Optional[List[str]] = None   # 显式参考（approved views）；缺省=自动确定性选择
    size_tier: str = "1K"
    ratio: str = "16:9"
    # 2026-09-17 验收轮：image provider 覆盖（agnes 默认；sensenova=商汤 u1.5-lite）
    image_provider: str = "agnes"


@router.post("/api/project/{project}/scene-system/views/{view_id}/generate")
async def generate_location_view(project: str, view_id: str,
                                 req: GenerateLocationViewRequest):
    """LocationView 候选生成（三阶段协议；provider 调用在锁外）。
    Base Location identity——不生成 SceneState 剧情状态图（§29）。"""
    try:
        receipt = await _visual_service(project).generate_location_view(
            view_id, expected_revision=req.expected_revision,
            candidate_count=req.candidate_count, seed=req.seed,
            reference_view_ids=req.reference_view_ids,
            size_tier=req.size_tier, ratio=req.ratio,
            agnes_client=deps.resolve_image_client(req.image_provider))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/scene-system/views/{view_id}/review")
async def review_location_view(project: str, view_id: str):
    """对当前 committed 候选执行六维度 Vision Review（canonical 零写入；证据入 review_records）。"""
    try:
        receipt = await _visual_service(project).review_location_view_candidates(view_id)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ApproveLocationViewRequest(BaseModel):
    candidate_index: int = 0
    expected_revision: str                # scene view revision（OCC）
    expected_resources_document_revision: str   # resources document revision（第二个 canonical 的 OCC）


@router.post("/api/project/{project}/scene-system/views/{view_id}/approve")
async def approve_location_view(project: str, view_id: str, req: ApproveLocationViewRequest):
    """显式 Approval（cross-canonical saga）：注册 Resources ReferenceAsset + attach。
    review pass ≠ 自动 approve；approve 需 pass 证据 + 双 canonical OCC 期望值。"""
    try:
        receipt = await _visual_service(project).approve_location_view(
            view_id, candidate_index=req.candidate_index,
            expected_revision=req.expected_revision,
            expected_resources_document_revision=req.expected_resources_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RevokeViewApprovalRequest(BaseModel):
    expected_revision: str
    reason: str = ""


@router.post("/api/project/{project}/scene-system/views/{view_id}/revoke")
async def revoke_view_approval(project: str, view_id: str, req: RevokeViewApprovalRequest):
    """解除 approved（approval_status→rejected）；历史 records 与 ReferenceAsset 全保留。"""
    try:
        receipt = await _visual_service(project).revoke_view_approval(
            view_id, expected_revision=req.expected_revision, reason=req.reason)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 证据流只读端点（generation/review/approval records）============
@router.get("/api/project/{project}/scene-system/views/{view_id}/generations")
async def read_generation_records(project: str, view_id: str,
                                  limit: Optional[int] = Query(default=None)):
    try:
        from src.scene_system import generation_records as _gr
        pdir = get_project_manager().get_project_dir(project)
        recs = _gr.read_records(pdir, view_id=view_id, limit=limit)
        # P1 §13/§14：附加 computed style 兼容结论（不落盘、不 mutation）。
        # legacy record → unknown + generated_before_visual_profile=true（不伪造）。
        from src.visual_profile import load_profile_or_none
        from src.visual_profile.models import (style_compatibility_of_basis,
                                               VisualProfileSchemaError)
        try:
            vp = load_profile_or_none(pdir)
            cur_fp = None
            from src.visual_profile.models import visual_profile_fingerprint
            if vp is not None:
                cur_fp = visual_profile_fingerprint(vp)
        except VisualProfileSchemaError:
            cur_fp = "__unreadable__"
        for rec in recs:
            basis = rec.get("basis") or {}
            if cur_fp == "__unreadable__":
                rec["style_compatibility"] = {
                    "style_compatibility": "visual_profile_unreadable",
                    "generated_before_visual_profile": None,
                    "visual_profile_fingerprint": basis.get("visual_profile_fingerprint")}
            else:
                rec["style_compatibility"] = style_compatibility_of_basis(basis, cur_fp)
        return {"generation_records": recs}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/views/{view_id}/reviews")
async def read_review_records(project: str, view_id: str,
                              limit: Optional[int] = Query(default=None)):
    try:
        from src.scene_system import generation_records as _gr
        recs = _gr.read_records(get_project_manager().get_project_dir(project),
                                view_id=view_id, limit=limit,
                                path=_gr.review_records_path(
                                    get_project_manager().get_project_dir(project)))
        return {"review_records": recs}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/views/{view_id}/approvals")
async def read_approval_records(project: str, view_id: str):
    try:
        from src.scene_system import generation_records as _gr
        pdir = get_project_manager().get_project_dir(project)
        recs = _gr.read_records(pdir, view_id=view_id,
                                path=_gr.approval_records_path(pdir))
        return {"approval_records": recs}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/scene-system/recovery/run")
async def run_recovery(project: str):
    """手动触发孤儿 generating 清理（幂等；startup 已自动挂载，此端点供运维/测试）。"""
    try:
        from src.scene_system.recovery import recover_orphaned_view_generations
        pdir = get_project_manager().get_project_dir(project)
        receipt = await recover_orphaned_view_generations(pdir, project)
        _raise_for_receipt(receipt)
        return receipt
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ Phase S2B：SceneInstanceView（SceneInstance 视觉状态，§34）============
class CreateSceneInstanceViewRequest(BaseModel):
    scene_instance_id: str
    location_view_id: str
    label: str = ""
    expected_document_revision: str


@router.post("/api/project/{project}/scene-system/scene-instance-views")
async def create_scene_instance_view(project: str, req: CreateSceneInstanceViewRequest):
    """创建 SceneInstanceView（身份 = (scene_instance, location_view) 对；§2.C location 一致性）。"""
    try:
        receipt = await _visual_service(project).create_scene_instance_view(
            scene_instance_id=req.scene_instance_id,
            location_view_id=req.location_view_id, label=req.label,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/scene-instance-views")
async def list_scene_instance_views(project: str,
                                    scene_instance_id: Optional[str] = Query(default=None),
                                    location_view_id: Optional[str] = Query(default=None)):
    try:
        svc = _visual_service(project)
        return {"scene_instance_views": svc.list_scene_instance_views(
            scene_instance_id=scene_instance_id, location_view_id=location_view_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}")
async def read_scene_instance_view(project: str, sciv_id: str):
    """read 模型（§34）：base location view + 当前 ViewRelevantSceneState 投影 +
    审查摘要 + is_stale/stale_reasons（derived，不自动 mutation）。"""
    try:
        d = _visual_service(project).read_scene_instance_view_status(sciv_id)
        if d is None:
            raise HTTPException(status_code=404,
                                detail=f"SceneInstanceView 不存在: {sciv_id}")
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class GenerateSceneInstanceViewRequest(BaseModel):
    expected_revision: str
    candidate_count: int = 2
    seed: Optional[int] = None
    include_peer: bool = True
    size_tier: str = "1K"
    ratio: str = "16:9"
    # 2026-09-17 验收轮：image provider 覆盖（agnes 默认；sensenova=商汤 u1.5-lite）
    image_provider: str = "agnes"


@router.post("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}/generate")
async def generate_scene_instance_view(project: str, sciv_id: str,
                                       req: GenerateSceneInstanceViewRequest):
    """SceneInstanceView 候选生成（三阶段；base approved 母版强制；无人物）。"""
    try:
        receipt = await _visual_service(project).generate_scene_instance_view(
            sciv_id, expected_revision=req.expected_revision,
            candidate_count=req.candidate_count, seed=req.seed,
            include_peer=req.include_peer, size_tier=req.size_tier, ratio=req.ratio,
            agnes_client=deps.resolve_image_client(req.image_provider))
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}/review")
async def review_scene_instance_view(project: str, sciv_id: str):
    """七维度 Vision Review（逐项 state/prop evidence；canonical 零写入）。"""
    try:
        from src.scene_system.instance_view_review import (
            review_scene_instance_view_candidates as _impl)
        receipt = await _impl(get_project_manager().get_project_dir(project), sciv_id)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ApproveSceneInstanceViewRequest(BaseModel):
    candidate_index: int = 0
    expected_revision: str
    expected_resources_document_revision: str


@router.post("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}/approve")
async def approve_scene_instance_view(project: str, sciv_id: str,
                                      req: ApproveSceneInstanceViewRequest):
    """显式 Approval（cross-canonical saga；review pass ≠ 自动 approve）。"""
    try:
        receipt = await _visual_service(project).approve_scene_instance_view(
            sciv_id, candidate_index=req.candidate_index,
            expected_revision=req.expected_revision,
            expected_resources_document_revision=req.expected_resources_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RevokeSceneInstanceViewRequest(BaseModel):
    expected_revision: str
    reason: str = ""


@router.post("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}/revoke")
async def revoke_scene_instance_view(project: str, sciv_id: str,
                                     req: RevokeSceneInstanceViewRequest):
    """解除 approved（历史保留）。"""
    try:
        receipt = await _visual_service(project).revoke_scene_instance_view_approval(
            sciv_id, expected_revision=req.expected_revision, reason=req.reason)
        _raise_for_receipt(receipt)
        return receipt
    except SceneSystemSchemaError as e:
        raise HTTPException(status_code=500, detail=f"scene_system.json 数据错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 实例视图证据流只读端点（generation/review/approval records；S-1/S-8）============
# 与 LocationView 的同名端点同口径；仅读证据流文件，canonical 零写入。
@router.get("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}/generations")
async def read_instance_view_generation_records(project: str, sciv_id: str,
                                                limit: Optional[int] = Query(default=None)):
    try:
        from src.scene_system import generation_records as _gr
        pdir = get_project_manager().get_project_dir(project)
        recs = _gr.read_records(pdir, scene_instance_view_id=sciv_id, limit=limit)
        # P1 §13/§14：附加 computed style 兼容结论（不落盘、不 mutation）——与 views 端点同口径。
        from src.visual_profile import load_profile_or_none
        from src.visual_profile.models import (style_compatibility_of_basis,
                                               VisualProfileSchemaError)
        try:
            vp = load_profile_or_none(pdir)
            cur_fp = None
            from src.visual_profile.models import visual_profile_fingerprint
            if vp is not None:
                cur_fp = visual_profile_fingerprint(vp)
        except VisualProfileSchemaError:
            cur_fp = "__unreadable__"
        for rec in recs:
            basis = rec.get("basis") or {}
            if cur_fp == "__unreadable__":
                rec["style_compatibility"] = {
                    "style_compatibility": "visual_profile_unreadable",
                    "generated_before_visual_profile": None,
                    "visual_profile_fingerprint": basis.get("visual_profile_fingerprint")}
            else:
                rec["style_compatibility"] = style_compatibility_of_basis(basis, cur_fp)
        return {"generation_records": recs}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}/reviews")
async def read_instance_view_review_records(project: str, sciv_id: str,
                                            limit: Optional[int] = Query(default=None)):
    try:
        from src.scene_system.instance_view_review import (
            read_instance_view_review_records as _impl)
        recs = _impl(get_project_manager().get_project_dir(project),
                     scene_instance_view_id=sciv_id)
        if limit is not None and limit >= 0:
            recs = recs[-limit:]
        return {"review_records": recs}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/scene-system/scene-instance-views/{sciv_id}/approvals")
async def read_instance_view_approval_records(project: str, sciv_id: str):
    try:
        from src.scene_system import generation_records as _gr
        pdir = get_project_manager().get_project_dir(project)
        recs = _gr.read_records(pdir, scene_instance_view_id=sciv_id,
                                path=_gr.approval_records_path(pdir))
        return {"approval_records": recs}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
