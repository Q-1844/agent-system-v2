# src/scene_system/store.py
# Scene System V1 — Phase S1 SceneSystemStore。
#
# 独立 canonical companion：projects/{project}/output/scene_system.json。
# 🔴 不修改 src/core/store.py::ProjectStore 语义 / project.json schema。
# 🔴 不触碰 resources.json（Resources V1 冻结区）。
# 复用底层（与 Resources 同款成熟经验）：
#   src/core/atomic.atomic_write_json（文件级原子完整）
#   src/core/write_lock.project_write_lock（per-project async 锁，同 project_name key
#   与 project.json / resources.json 写串行化——不创建第二套 project lock）
#
# 要求（对齐 ResourceStore 契约）：
#   A. 无 scene_system.json → load 返回空 SceneSystemDocument；read 绝不创建文件（零副作用）。
#   B. 首次真正 write 才创建 scene_system.json。
#   C. 写入 atomic（atomic_write_json）。
#   D. 复用 project_write_lock（同 project_name key）。
#   E. 序列化 deterministic（fingerprint 用 sort_keys canonical json；落盘保持 key 顺序）。
#   F. 未知 schema_version fail closed（SceneSystemSchemaError）。
#   G. 坏 JSON / 缺字段 → 明确错误，绝不静默重置为空（防空间事实数据丢失）。
from __future__ import annotations
from pathlib import Path
from typing import Optional

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock
from .models import SceneSystemDocument, SceneSystemSchemaError


class SceneSystemStore:
    """projects/{project}/output/scene_system.json canonical store（纯 sync，对齐 ProjectStore）。

    load/save/exists。读零副作用；写才建文件（atomic）。
    project_name 用于写路径获取 per-project 锁（project_write_lock）。
    """

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "scene_system.json"
        self.project_name = project_name or self.dir.name

    # ============ lock（service async 写路径用）============
    def write_lock(self):
        """返回 per-project asyncio.Lock（复用 project_write_lock，同 project_name key）。
        所有 load→validate→OCC→mutate→save 必须在该锁的短临界区内（§18）。"""
        return project_write_lock(self.project_name)

    # ============ load / save ============
    def _load_raw(self) -> Optional[dict]:
        """读原始 dict。无文件→None（不创建）。坏 JSON→SceneSystemSchemaError（不静默重置）。"""
        if not self.path.exists():
            return None
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as e:
            raise SceneSystemSchemaError(f"scene_system.json 读取失败（不自动重置）：{e}") from e
        try:
            return json_loads(text)
        except Exception as e:
            raise SceneSystemSchemaError(
                f"scene_system.json 损坏（不自动重置为空，防空间事实数据丢失）：{e}") from e

    def load(self) -> SceneSystemDocument:
        """加载。无文件→空 SceneSystemDocument（零副作用，不写盘）。
        有文件→from_dict（未知 schema_version / 坏字段 fail closed）。"""
        raw = self._load_raw()
        if raw is None:
            return SceneSystemDocument()
        if not isinstance(raw, dict):
            raise SceneSystemSchemaError("scene_system.json 顶层不是 JSON object（不自动重置）")
        return SceneSystemDocument.from_dict(raw)

    def save(self, doc: SceneSystemDocument) -> None:
        """显式 atomic 写入。首次写创建 scene_system.json。"""
        atomic_write_json(self.path, doc.to_dict())

    def exists(self) -> bool:
        """scene_system.json 是否存在（无需 load 即可判断，供零副作用探测）。"""
        return self.path.exists()


# json.loads 局部别名（便于 _load_raw 异常归类；不引入新依赖）
def json_loads(text: str):
    import json
    return json.loads(text)
