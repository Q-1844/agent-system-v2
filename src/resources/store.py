# src/resources/store.py
# Production Resources V1 — Phase 1B ResourceStore + legacy Character.views adapter.
#
# 独立 canonical companion：projects/{project}/output/resources.json。
# 🔴 不修改 src/core/store.py::ProjectStore 语义 / project.json schema。
# 复用底层：src/core/atomic.atomic_write_json（文件级原子完整）；
#           src/core/write_lock.project_write_lock（per-project async 锁，Phase 1C 异步写路径接入，
#           同 project_name key 与 project.json 写串行化——Phase 1B 只读 API + sync 测试写无并发写者）。
#
# 要求（Phase 1B §6）：
#   A. 无 resources.json → load 返回空 ResourceDocument；read/list 绝不创建文件（零副作用）。
#   B. 首次真正 write（put/update/delete）才创建 resources.json。
#   C. 写入 atomic（atomic_write_json）。
#   D. 复用 project_write_lock（同 project_name key；Phase 1C async 写路径）。
#   E. 序列化 deterministic（fingerprint 用 sort_keys canonical json；落盘保持 key 顺序）。
#   F. 未知 schema_version fail closed（ResourceSchemaError）。
#   G. 坏 JSON / 缺字段 → 明确错误，绝不静默重置为空（防资产丢失）。
from __future__ import annotations
import time
from pathlib import Path
from typing import Optional, List, Callable

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock   # Phase 1C async 写路径复用
from .models import (
    ResourceDocument, CharacterLook, CharacterReferenceComposition, Clothing, Prop,
    ReferenceAsset, ResourceSchemaError, ResourceScope, ResourceSource,
)


class ResourceStore:
    """projects/{project}/output/resources.json canonical store（纯 sync，对齐 ProjectStore）。

    load/save/get/list/put/update/delete。读零副作用；写才建文件（atomic）。
    project_name 用于 Phase 1C async 写路径获取 per-project 锁（project_write_lock）。
    """

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "resources.json"
        self.project_name = project_name or self.dir.name

    # ============ lock（Phase 1C async 写路径用；Phase 1B 不触发）============
    def write_lock(self):
        """返回 per-project asyncio.Lock（复用 project_write_lock，同 project_name key）。
        Phase 1C 异步写路径：`async with store.write_lock(): store.put_look(...)`。
        Phase 1B 只读 API + sync 测试写无并发 project.json 写者，无需持锁。"""
        return project_write_lock(self.project_name)

    # ============ load / save ============
    def _load_raw(self) -> Optional[dict]:
        """读原始 dict。无文件→None（不创建）。坏 JSON→ResourceSchemaError（不静默重置）。"""
        if not self.path.exists():
            return None
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as e:
            raise ResourceSchemaError(f"resources.json 读取失败（不自动重置）：{e}") from e
        try:
            return json_loads(text)
        except Exception as e:
            raise ResourceSchemaError(f"resources.json 损坏（不自动重置为空，防资产丢失）：{e}") from e

    def load(self) -> ResourceDocument:
        """加载。无文件→空 ResourceDocument（零副作用，不写盘）。
        有文件→from_dict（未知 schema_version / 坏字段 fail closed）。"""
        raw = self._load_raw()
        if raw is None:
            return ResourceDocument()
        if not isinstance(raw, dict):
            raise ResourceSchemaError("resources.json 顶层不是 JSON object（不自动重置）")
        return ResourceDocument.from_dict(raw)

    def save(self, doc: ResourceDocument) -> None:
        """显式 atomic 写入。首次写创建 resources.json。"""
        atomic_write_json(self.path, doc.to_dict())

    def exists(self) -> bool:
        """resources.json 是否存在（无需 load 即可判断，供零副作用探测）。"""
        return self.path.exists()

    # ============ get ============
    def get_look(self, look_id: str) -> Optional[CharacterLook]:
        return self.load().looks.get(look_id)

    def get_clothing(self, clothing_id: str) -> Optional[Clothing]:
        return self.load().clothings.get(clothing_id)

    def get_prop(self, prop_id: str) -> Optional[Prop]:
        return self.load().props.get(prop_id)

    def get_composition(self, composition_id: str) -> Optional[CharacterReferenceComposition]:
        return self.load().compositions.get(composition_id)

    def get_reference_asset(self, asset_id: str) -> Optional[ReferenceAsset]:
        return self.load().reference_assets.get(asset_id)

    # ============ list（store 返回 typed 对象；service 做 lightweight summary）============
    def list_looks(self) -> List[CharacterLook]:
        return list(self.load().looks.values())

    def list_clothings(self) -> List[Clothing]:
        return list(self.load().clothings.values())

    def list_props(self) -> List[Prop]:
        return list(self.load().props.values())

    def list_compositions(self) -> List[CharacterReferenceComposition]:
        return list(self.load().compositions.values())

    def list_reference_assets(self) -> List[ReferenceAsset]:
        return list(self.load().reference_assets.values())

    # ============ put（create-or-replace by id）============
    def put_look(self, look: CharacterLook) -> CharacterLook:
        doc = self.load()
        doc.looks[look.look_id] = look
        self.save(doc)
        return look

    def put_clothing(self, clothing: Clothing) -> Clothing:
        doc = self.load()
        doc.clothings[clothing.clothing_id] = clothing
        self.save(doc)
        return clothing

    def put_prop(self, prop: Prop) -> Prop:
        doc = self.load()
        doc.props[prop.prop_id] = prop
        self.save(doc)
        return prop

    def put_reference_asset(self, asset: ReferenceAsset) -> ReferenceAsset:
        doc = self.load()
        doc.reference_assets[asset.asset_id] = asset
        self.save(doc)
        return asset

    # ============ update（read-modify-write 单对象）============
    def update_look(self, look_id: str, mutator: Callable[[CharacterLook], None]) -> CharacterLook:
        doc = self.load()
        lk = doc.looks.get(look_id)
        if lk is None:
            raise KeyError(f"look not found: {look_id}")
        mutator(lk)
        lk.updated_at = time.time()
        self.save(doc)
        return lk

    def update_clothing(self, clothing_id: str, mutator: Callable[[Clothing], None]) -> Clothing:
        doc = self.load()
        c = doc.clothings.get(clothing_id)
        if c is None:
            raise KeyError(f"clothing not found: {clothing_id}")
        mutator(c)
        c.updated_at = time.time()
        self.save(doc)
        return c

    def update_prop(self, prop_id: str, mutator: Callable[[Prop], None]) -> Prop:
        doc = self.load()
        p = doc.props.get(prop_id)
        if p is None:
            raise KeyError(f"prop not found: {prop_id}")
        mutator(p)
        p.updated_at = time.time()
        self.save(doc)
        return p

    def update_reference_asset(self, asset_id: str, mutator: Callable[[ReferenceAsset], None]) -> ReferenceAsset:
        doc = self.load()
        a = doc.reference_assets.get(asset_id)
        if a is None:
            raise KeyError(f"reference asset not found: {asset_id}")
        mutator(a)
        a.updated_at = time.time()
        self.save(doc)
        return a

    # ============ delete ============
    def delete_look(self, look_id: str) -> bool:
        doc = self.load()
        if look_id not in doc.looks:
            return False
        del doc.looks[look_id]
        self.save(doc)
        return True

    def delete_clothing(self, clothing_id: str) -> bool:
        doc = self.load()
        if clothing_id not in doc.clothings:
            return False
        del doc.clothings[clothing_id]
        self.save(doc)
        return True

    def delete_prop(self, prop_id: str) -> bool:
        doc = self.load()
        if prop_id not in doc.props:
            return False
        del doc.props[prop_id]
        self.save(doc)
        return True

    def delete_reference_asset(self, asset_id: str) -> bool:
        """删除 ReferenceAsset。注意：不自动清理 Look/Clothing/Prop 中对该 asset_id 的引用
        （referential integrity sweep 属 Phase 1C+；service read 对缺失 asset 容错）。"""
        doc = self.load()
        if asset_id not in doc.reference_assets:
            return False
        del doc.reference_assets[asset_id]
        self.save(doc)
        return True


# ============ legacy Character.views 只读 adapter（§8）============
def legacy_character_references(project_dir, character_name: str) -> List[ReferenceAsset]:
    """把现有 Character.views[front/side/back] 映射成统一的 ReferenceAsset-compatible 读视图。

    🔴 只读兼容视图：
    - 不给 legacy view 分配并持久化 ref_id（用合成 id `legacy:{character_name}:{view}`，读时派生，不入库）。
    - 不自动创建 CharacterLook / Base Look。
    - 不写 resources.json（不调 ResourceStore.save）。
    - 不修改 Character（ProjectStore 只 load 不 save）。

    目标：证明以后 Resources UI / Look Generator 可以用统一方式读取旧 Character 三视图。
    用户以后若显式创建 Base Look，那时才生成真正稳定的 look_id/ref_id。
    """
    from src.core.store import ProjectStore

    pdir = Path(project_dir)
    try:
        project = ProjectStore(pdir).load()
    except FileNotFoundError:
        return []

    char = project.get_character(character_name)
    if char is None:
        return []

    out: List[ReferenceAsset] = []
    for view in ("front", "side", "back"):
        a = char.views.get(view)
        if a is None:
            continue
        # 合成 id（派生，非持久化）；source=legacy 标记来源；purpose=identity（三视图=身份参考）
        out.append(ReferenceAsset(
            asset_id=f"legacy:{character_name}:{view}",
            scope=ResourceScope.PROJECT.value,
            media_type="image",
            local=a.local,
            url=a.url,
            checksum=None,                  # legacy Asset 无 checksum，不臆造
            view=view,
            purpose="identity",
            background_type="",             # 不臆造：local 可能是 raw（router bind）或 seg（handler），无法可靠区分
            source="legacy",
            origin=None,                    # legacy 无 lineage
            created_at=a.updated_at or 0.0,
            updated_at=a.updated_at or 0.0,
        ))
    return out


# json.loads 局部别名（便于 _load_raw 异常归类；不引入新依赖）
def json_loads(text: str):
    import json
    return json.loads(text)
