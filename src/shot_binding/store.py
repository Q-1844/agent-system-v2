# src/shot_binding/store.py
# Shot Production Binding V1 — Phase S3B.1 ShotBindingStore。
#
# 独立 canonical companion：projects/{project}/output/shot_production_bindings.json。
# 🔴 不修改 project.json / Shot dataclass / resources.json / scene_system.json。
# 复用底层（与 Resources / Scene System 同款成熟经验）：
#   src/core/atomic.atomic_write_json（文件级原子完整 + 自建父目录 + Windows 重试）
#   src/core/write_lock.project_write_lock（per-project async 锁，同 project_name key
#   与 project.json / resources.json / scene_system.json / project_profile.json 写串行化
#   ——不创建第二套 project lock）
#
# 要求（对齐 SceneSystemStore 契约，7.md S3B.1 §十一/§十二/§十三）：
#   A. 无 shot_production_bindings.json → load 返回空 document；read 绝不创建文件
#      （零副作用；"no bindings"≠ error）。
#   B. 首次真正 write 才创建文件（atomic）。
#   C. 写入 atomic（atomic_write_json）。
#   D. 复用 project_write_lock（同 project_name key）。
#   E. 未知 schema_version fail closed（ShotBindingSchemaError）——不自动降级/
#      重置/静默覆盖。
#   F. 坏 JSON → 明确错误，绝不静默重置为空。
from __future__ import annotations
from pathlib import Path
from typing import Optional

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock

from .models import ShotBindingDocument, ShotBindingSchemaError


class ShotBindingStore:
    """projects/{project}/output/shot_production_bindings.json canonical store
    （纯 sync，对齐 ProjectStore / SceneSystemStore）。

    load/save/exists/write_lock。读零副作用；写才建文件（atomic）。
    project_name 用于写路径获取 per-project 锁（project_write_lock）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "shot_production_bindings.json"
        self.project_name = project_name or self.dir.name

    # ============ lock（service async 写路径用）============
    def write_lock(self):
        """返回 per-project asyncio.Lock（复用 project_write_lock，同 project_name key）。
        所有 load→validate→OCC→mutate→save 必须在该锁的短临界区内。"""
        return project_write_lock(self.project_name)

    # ============ load / save ============
    def _load_raw(self) -> Optional[dict]:
        """读原始 dict。无文件→None（不创建）。坏 JSON→ShotBindingSchemaError
        （不静默重置）。"""
        if not self.path.exists():
            return None
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as e:
            raise ShotBindingSchemaError(
                f"shot_production_bindings.json 读取失败（不自动重置）：{e}") from e
        try:
            import json
            return json.loads(text)
        except Exception as e:
            raise ShotBindingSchemaError(
                f"shot_production_bindings.json 损坏（不自动重置为空）：{e}") from e

    def load(self) -> ShotBindingDocument:
        """加载。无文件→空 ShotBindingDocument（零副作用，不写盘——纯读原则）。
        有文件→from_dict（未知 schema_version / 坏字段 fail closed）。"""
        raw = self._load_raw()
        if raw is None:
            return ShotBindingDocument()
        if not isinstance(raw, dict):
            raise ShotBindingSchemaError(
                "shot_production_bindings.json 顶层不是 JSON object（不自动重置）")
        return ShotBindingDocument.from_dict(raw)

    def save(self, doc: ShotBindingDocument) -> None:
        """显式 atomic 写入。首次写创建 shot_production_bindings.json。"""
        atomic_write_json(self.path, doc.to_dict())

    def exists(self) -> bool:
        """bindings 文件是否存在（无需 load 即可判断，供零副作用探测）。"""
        return self.path.exists()
