"""资源路由：Production Resources V1 Phase 1B —— 只读 HTTP API。

所有端点放在新命名空间 /api/project/{project}/resources|looks|clothings|props，
不与现有 routers/characters.py（/character 单数、/characters/full）路径空间冲突。

Phase 1B 只开放 read API（GET）：
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

🔴 本轮不开放任何写 API（POST/PUT/PATCH/DELETE 一律不挂）。
   不为测试方便暴露未冻结的 mutation 端点。写由 service 内部方法供测试调用。
"""
from fastapi import APIRouter, HTTPException, Query

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


@router.get("/api/project/{project}/resources")
async def list_resources(
    project: str,
    type: str = Query(default="all", description="look|clothing|prop|reference_asset|all"),
):
    """统一资源轻量列表（无 base64 / 图片 bytes / receipt / 巨大 prompt）。"""
    try:
        return {"resources": _service(project).list_resources(type if type != "all" else None)}
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
    """Look 轻量列表。"""
    try:
        return {"looks": _service(project).list_looks(character_name)}
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
        return {"clothings": _service(project).list_clothings()}
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
        return {"props": _service(project).list_props()}
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
