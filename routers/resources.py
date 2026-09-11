"""资源路由：Production Resources V1 —— Phase 1B 只读 + Phase 1C1 有限写 API。

所有端点放在新命名空间 /api/project/{project}/resources|looks|clothings|props，
不与现有 routers/characters.py（/character 单数、/characters/full）路径空间冲突。

Phase 1B read API（GET）：
- GET  /api/project/{project}/resources                  统一资源列表（?type=look|clothing|prop|reference_asset|all）
- GET  /api/project/{project}/resources/{resource_id}     读单资源（按 id 前缀 dispatch）
- GET  /api/project/{project}/resources/legacy-character-references/{character_name}
                                                          legacy Character.views 只读 adapter
- GET  /api/project/{project}/looks                      Look 列表（?character_name=）
- GET  /api/project/{project}/looks/{look_id}            读 Look（完整 typed relationship）
- GET  /api/project/{project}/clothings                 Clothing 列表
- GET  /api/project/{project}/clothings/{clothing_id}    读 Clothing
- GET  /api/project/{project}/props                      Prop 列表
- GET  /api/project/{project}/props/{prop_id}            读 Prop

Phase 1C1 写 API（§22，每个 mutation 必须接 expected_revision）：
- POST   /api/project/{project}/clothings                                创建 Clothing（expected_document_revision）
- PATCH  /api/project/{project}/clothings/{clothing_id}                  更新（expected_revision；rename=改 name）
- DELETE /api/project/{project}/clothings/{clothing_id}                  删除（expected_revision；被 Look 引用→409 resource_in_use）
- POST   /api/project/{project}/reference-assets                         注册已有 URL / 项目内本地图片（expected_document_revision）
- POST   /api/project/{project}/clothings/{clothing_id}/references       绑定 ReferenceAsset（expected_revision=clothing）
- DELETE /api/project/{project}/clothings/{clothing_id}/references/{asset_id}   解绑（expected_revision=clothing）
- POST   /api/project/{project}/looks                                    创建 Look（expected_document_revision）
- PATCH  /api/project/{project}/looks/{look_id}                          更新（expected_revision）
- POST   /api/project/{project}/looks/{look_id}/generate                 Look 候选生成（三阶段协议，expected_revision）

🔴 router 只做 request validation / HTTP mapping；lock/OCC/prompt/generation 全在 service。
   本轮不开放：Look delete / Prop 写 API / 任何 approve/reject / 任何 Agent 接线。
"""
from typing import Optional, List

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from deps import get_project_manager
from src.resources.service import ResourceService
from src.resources.models import ResourceSchemaError

router = APIRouter()


def _service(project_name: str) -> ResourceService:
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_name}")
    return ResourceService(project_dir, project_name)


def _write_service(project_name: str):
    """写端点用：ResourceWriteService（lock/OCC/receipt 在 service 层）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_name}")
    from src.resources.write_service import ResourceWriteService
    return ResourceWriteService(project_dir, project_name)


# receipt error 码 → HTTP status（§22：router 只做 HTTP mapping）
_ERROR_STATUS = {
    "stale_revision": 409,
    "stale_generation_basis": 409,
    "resource_in_use": 409,
    "not_found": 404,
    "character_not_found": 404,
    "clothing_not_found": 404,
    "asset_not_found": 404,
    "missing_character_reference": 422,
    "invalid_reference_input": 400,
    "validation_error": 400,
    "invalid_candidate_count": 400,
    "provider_failed": 502,
    "generation_in_progress": 409,   # Phase 1C1.1 per-look single-flight
    "review_not_passed": 409,        # Phase 1C2：approve 需 pass 证据
    "not_in_candidates": 400,
    "no_candidates": 400,
}


def _raise_for_receipt(receipt: dict):
    """业务 receipt（ok=False）→ 对应 HTTP 状态码；body 保持完整 receipt 结构。"""
    if receipt.get("ok"):
        return
    code = receipt.get("error") or "internal_error"
    status = _ERROR_STATUS.get(code, 500)
    raise HTTPException(status_code=status, detail=receipt)


@router.get("/api/project/{project}/resources")
async def list_resources(
    project: str,
    type: str = Query(default="all", description="look|clothing|prop|reference_asset|all"),
):
    """统一资源轻量列表（无 base64 / 图片 bytes / receipt / 巨大 prompt）。
    envelope 附 document_revision（创建类 mutation 的 OCC 期望值来源，Phase 1C1）。"""
    try:
        svc = _service(project)
        return {"resources": svc.list_resources(type if type != "all" else None),
                "document_revision": svc.document_revision()}
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/resources/legacy-character-references/{character_name}")
async def legacy_character_references(project: str, character_name: str):
    """legacy Character.views 只读 adapter：把旧三视图映射成 ReferenceAsset-compatible 读视图。
    不修改 Character、不写 resources.json、不建 Look。"""
    try:
        refs = _service(project).legacy_character_references(character_name)
        return {"character_name": character_name, "references": refs}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/resources/{resource_id}")
async def read_resource(project: str, resource_id: str):
    """读单资源（按 id 前缀 dispatch：look_/clothing_/prop_/ref_）。返回完整 typed relationship。"""
    try:
        res = _service(project).read_resource(resource_id)
        if res is None:
            raise HTTPException(status_code=404, detail=f"资源不存在: {resource_id}")
        return res
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/looks")
async def list_looks(
    project: str,
    character_name: str = Query(default=None, description="按角色名过滤"),
):
    """Look 轻量列表。envelope 附 document_revision（OCC 期望值来源）。"""
    try:
        svc = _service(project)
        return {"looks": svc.list_looks(character_name),
                "document_revision": svc.document_revision()}
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/looks/{look_id}")
async def read_look(project: str, look_id: str):
    """读 Look：含 reference_assets / clothing / cover 反向 resolve。"""
    try:
        res = _service(project).read_look(look_id)
        if res is None:
            raise HTTPException(status_code=404, detail=f"Look 不存在: {look_id}")
        return res
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/clothings")
async def list_clothings(project: str):
    try:
        svc = _service(project)
        return {"clothings": svc.list_clothings(),
                "document_revision": svc.document_revision()}
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/clothings/{clothing_id}")
async def read_clothing(project: str, clothing_id: str):
    try:
        res = _service(project).read_clothing(clothing_id)
        if res is None:
            raise HTTPException(status_code=404, detail=f"Clothing 不存在: {clothing_id}")
        return res
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/props")
async def list_props(project: str):
    try:
        svc = _service(project)
        return {"props": svc.list_props(),
                "document_revision": svc.document_revision()}
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project}/props/{prop_id}")
async def read_prop(project: str, prop_id: str):
    try:
        res = _service(project).read_prop(prop_id)
        if res is None:
            raise HTTPException(status_code=404, detail=f"Prop 不存在: {prop_id}")
        return res
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================================
# Phase 1C1 写 API（§22）。router 只做 HTTP mapping；lock/OCC/validation/receipt 全在 service。
# 每个 mutation 必须接 expected_revision（更新/删除=resource revision；创建=document revision）。
# =====================================================================
class CreateClothingRequest(BaseModel):
    name: str
    description: str = ""
    design_traits: str = ""
    materials: Optional[List[str]] = None
    colors: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    expected_document_revision: str


class RegisterReferenceAssetRequest(BaseModel):
    local: Optional[str] = None      # 项目内相对路径（与 url 二选一）
    url: Optional[str] = None
    view: str = ""                   # 仅显式提供才写入，不从文件名猜
    purpose: str = ""
    source: str = "manual"
    background_type: str = ""
    expected_document_revision: str


class AttachClothingReferenceRequest(BaseModel):
    asset_id: str
    expected_revision: str           # clothing 的 resource revision


class CreateLookRequest(BaseModel):
    character_name: str
    label: str = ""
    clothing_id: Optional[str] = None
    appearance_description: str = ""
    appearance_delta: str = ""
    expected_document_revision: str


class GenerateLookCandidatesRequest(BaseModel):
    expected_revision: str              # look 的 resource revision（entry OCC）
    candidate_count: int = 1            # 硬上限 4（service 校验）
    view_purpose: str = ""
    seed: Optional[int] = None


@router.post("/api/project/{project}/clothings")
async def create_clothing(project: str, req: CreateClothingRequest):
    """创建 Clothing（不自动生成图片；approval_status=unreviewed）。"""
    try:
        receipt = await _write_service(project).create_clothing(
            name=req.name, description=req.description, design_traits=req.design_traits,
            materials=req.materials, colors=req.colors, tags=req.tags,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/clothings/{clothing_id}")
async def update_clothing(project: str, clothing_id: str, patch: dict = Body(...)):
    """更新 Clothing（rename=改 name，clothing_id 恒不变）。
    PATCH body 透传 dict → service 白名单/未知字段严格拒绝（expected_revision 由 router 剥离）。"""
    try:
        body = {k: v for k, v in patch.items() if k != "expected_revision"}
        receipt = await _write_service(project).update_clothing(
            clothing_id, body,
            expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/clothings/{clothing_id}")
async def delete_clothing(project: str, clothing_id: str,
                          expected_revision: str = Query(...)):
    """删除 Clothing。仍被 Look 引用 → 409 resource_in_use（无级联删除）。"""
    try:
        receipt = await _write_service(project).delete_clothing(
            clothing_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/reference-assets")
async def register_reference_asset(project: str, req: RegisterReferenceAssetRequest):
    """注册已有 URL 或项目内已有本地图片为 ReferenceAsset（不做上传；不存 base64）。"""
    try:
        receipt = await _write_service(project).register_reference_asset(
            local=req.local, url=req.url, view=req.view, purpose=req.purpose,
            source=req.source, background_type=req.background_type,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/clothings/{clothing_id}/references")
async def attach_clothing_reference(project: str, clothing_id: str,
                                    req: AttachClothingReferenceRequest):
    """给 Clothing 绑定 ReferenceAsset（只存 ID；幂等；approved 列表本轮不动）。"""
    try:
        receipt = await _write_service(project).attach_clothing_reference(
            clothing_id, req.asset_id, expected_revision=req.expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project}/clothings/{clothing_id}/references/{asset_id}")
async def detach_clothing_reference(project: str, clothing_id: str, asset_id: str,
                                    expected_revision: str = Query(...)):
    """解除 Clothing ↔ ReferenceAsset 绑定（幂等）。"""
    try:
        receipt = await _write_service(project).detach_clothing_reference(
            clothing_id, asset_id, expected_revision=expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/looks")
async def create_look(project: str, req: CreateLookRequest):
    """创建 CharacterLook（验证 Character 存在于 project canonical；不自动生成、不建 Base Look）。"""
    try:
        receipt = await _write_service(project).create_look(
            character_name=req.character_name, label=req.label,
            clothing_id=req.clothing_id,
            appearance_description=req.appearance_description,
            appearance_delta=req.appearance_delta,
            expected_document_revision=req.expected_document_revision)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/project/{project}/looks/{look_id}")
async def update_look(project: str, look_id: str, patch: dict = Body(...)):
    """更新 Look（label / clothing_id / appearance_*；character_name 身份 FK 不可改）。
    expected_revision 由 router 剥离后再透传（service 白名单严格拒绝未知字段）。"""
    try:
        body = {k: v for k, v in patch.items() if k != "expected_revision"}
        receipt = await _write_service(project).update_look(
            look_id, body,
            expected_revision=patch.get("expected_revision", ""))
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/looks/{look_id}/generate")
async def generate_look_candidates(project: str, look_id: str,
                                   req: GenerateLookCandidatesRequest):
    """CharacterLook 候选生成（三阶段协议：短锁 snapshot → 无锁图片生成 → 重锁验证 commit）。
    这是长操作（真实图片生成秒级~几十秒）；生成期间绝不持 project_write_lock。"""
    try:
        result = await _write_service(project).generate_look_candidates(
            look_id, expected_revision=req.expected_revision,
            candidate_count=req.candidate_count, view_purpose=req.view_purpose,
            seed=req.seed)
        _raise_for_receipt(result)
        return result
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================================
# Phase 1C2 — Visual Review + Approval（review pass ≠ approved，双向分开）
# =====================================================================
class ReviewLookRequest(BaseModel):
    asset_ids: Optional[List[str]] = None   # 缺省=该 Look 全部候选
    view_purpose: str = ""


class ApproveLookReferenceRequest(BaseModel):
    asset_id: str
    expected_revision: str                  # look 的 resource revision
    set_cover: Optional[bool] = None        # None=首个 approved 自动成为封面


class RevokeApprovalRequest(BaseModel):
    asset_id: str
    expected_revision: str


@router.post("/api/project/{project}/looks/{look_id}/review")
async def review_look_candidates(project: str, look_id: str, req: ReviewLookRequest):
    """视觉审查候选（身份一致/服装一致/视觉缺陷三维度）→ 独立 review evidence。
    不写 canonical；review pass 不产生任何 approved 状态。vision 模型 agnes-2.5-flash。"""
    try:
        project_dir = get_project_manager().get_project_dir(project)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    try:
        from src.resources.look_review import review_look_candidates as _review
        result = await _review(project_dir, look_id, asset_ids=req.asset_ids,
                               view_purpose=req.view_purpose)
        _raise_for_receipt(result)
        return result
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/looks/{look_id}/approve")
async def approve_look_reference(project: str, look_id: str, req: ApproveLookReferenceRequest):
    """显式审批：把某张候选放入 approved_reference_asset_ids。
    门槛：该候选必须有 overall=pass 的审查证据（review_not_passed → 409）。"""
    try:
        receipt = await _write_service(project).approve_look_reference(
            look_id, req.asset_id, expected_revision=req.expected_revision,
            set_cover=req.set_cover)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project}/looks/{look_id}/revoke-approval")
async def revoke_look_approval(project: str, look_id: str, req: RevokeApprovalRequest):
    """撤销审批（候选保留在 reference_asset_ids；approved 变空回 unreviewed）。"""
    try:
        receipt = await _write_service(project).revoke_look_approval(
            look_id, req.asset_id, expected_revision=req.expected_revision)
        _raise_for_receipt(receipt)
        return receipt
    except HTTPException:
        raise
    except ResourceSchemaError as e:
        raise HTTPException(status_code=500, detail=f"资源数据错误: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
