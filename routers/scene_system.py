"""Scene System V1 路由（Phase S1）——空间与场景事实系统 HTTP API。

命名空间 /api/project/{project}/scene-system/*：
🔴 不与现有 /scenes（Story Scene 对账保存）、/story、/resources、/character-design
   路径空间冲突。Scene System ≠ 现有 Story Scene（story_scene_id 单向引用，见
   src/scene_system/models.py 头部审计结论）。

读 API（GET，sync service、零副作用——绝不创建 scene_system.json）：
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

🔴 router 只做 request validation / HTTP mapping；业务逻辑全在 src/scene_system/service.py。
🔴 本轮不开放：任何图片生成 / Visual Review / Agent 工具接线。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

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
