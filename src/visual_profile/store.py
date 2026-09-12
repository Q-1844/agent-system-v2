# src/visual_profile/store.py
# Project Visual Profile V1 — Phase P1 §5：projects/{project}/output/project_profile.json
# canonical store。
#
# 🔴 读零副作用（P1 §5）：无文件 → load 返回 None（unspecified）；纯读绝不创建文件；
#    第一次真实设置才创建。
# 🔴 写 atomic（复用 src/core/atomic.atomic_write_json）+ project_write_lock
#    串行化 OCC check-then-write（同 project_name key，与 project.json / resources.json
#    写路径同锁族）。
# 🔴 坏 JSON / schema 未知 → VisualProfileSchemaError（不静默当 unspecified——
#    静默会把"文件坏了"伪装成"从未设置"，反向污染 fail closed 语义）。
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock

from .models import (
    ProjectVisualProfile,
    VisualProfileSchemaError,
    parse_visual_profile,
    profile_revision,
)


class VisualProfileStore:
    """project_profile.json 读写（纯 sync；OCC 判定在 service，store 只管字节）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "project_profile.json"
        self.project_name = project_name or self.dir.name

    def write_lock(self):
        return project_write_lock(self.project_name)

    def exists(self) -> bool:
        return self.path.exists()

    # ============ load ============
    def load_or_none(self) -> Optional[ProjectVisualProfile]:
        """无文件 → None（unspecified，零副作用不创建）。
        坏文件 → VisualProfileSchemaError（诚实，不静默当 unspecified）。"""
        if not self.path.exists():
            return None
        import json
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as e:
            raise VisualProfileSchemaError(f"project_profile.json 读取失败：{e}") from e
        except Exception as e:
            raise VisualProfileSchemaError(
                f"project_profile.json 不是合法 JSON（不静默重置）：{e}") from e
        return parse_visual_profile(raw)

    def load_revision_or_empty(self) -> str:
        """当前 revision；unspecified → ""（首次写入的 expected_revision 约定值）。"""
        p = self.load_or_none()
        return profile_revision(p) if p is not None else ""

    # ============ save（第一次真实设置才创建文件）============
    def save(self, profile: ProjectVisualProfile) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, profile.to_dict())


def load_profile_or_none(project_dir) -> Optional[ProjectVisualProfile]:
    """生成路径/basis 计算用的便捷只读入口。坏文件向上抛
    VisualProfileSchemaError（caller 转 fail closed receipt，绝不静默当 unspecified）。"""
    return VisualProfileStore(project_dir).load_or_none()
