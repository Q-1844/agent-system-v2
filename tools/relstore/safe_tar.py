# tools/relstore/safe_tar.py
# G2：安全 tar 检视/隔离解包/确定性打包。G1 原型（17/17 恶意样本通过）产品化：
# - 仅接受【未压缩 tar】（ustar/pax）；gz/xz/zst/zip 按 magic 明确拒绝（不调系统命令）
# - 白名单成员类型；穿越/盘符/UNC/保留名/NUL/重名(含大小写/Unicode)/长路径/类型冲突
# - 解包到全新随机目录，流式复制，不保留可执行位，不执行任何成员
# - 打包：排序、固定 mtime/uid/gid/mode、PAX、单根（确定性字节）
# - 分卷：只读流式重组（不写源仓；输出由调用方指定隔离路径）
from __future__ import annotations

import os
import re
import shutil
import tarfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .errors import CorruptArchive, UnsafeArchive, UnsupportedCompression

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(0, 10)),
                    *(f"LPT{i}" for i in range(0, 10))}
DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
SAFE_TYPES = {tarfile.DIRTYPE, tarfile.REGTYPE, tarfile.AREGTYPE}

# Windows MAX_PATH 260；为 \\?\ 与调用方拼预留余量，成员路径（相对）硬闸 230
MAX_COMPONENT = 255
MAX_REL_PATH = 230
DEFAULT_MAX_FILES = 20000
DEFAULT_MAX_TOTAL = 10 * 1024 ** 3
DEFAULT_MAX_FILE = 2 * 1024 ** 3 - 1
COMPRESSION_MAGIC = {
    b"\x1f\x8b": "gzip",
    b"\xfd7zXZ\x00": "xz",
    b"(\xb5/\xfd": "zstd",
    b"PK\x03\x04": "zip",
    b"PK\x05\x06": "zip(empty)",
    b"BZh": "bzip2",
}


@dataclass
class Plan:
    source: Path
    root_prefix: str
    files: list = field(default_factory=list)   # (rel_posix, TarInfo)
    dirs: list = field(default_factory=list)
    total_bytes: int = 0
    file_count: int = 0
    max_file: tuple = (0, "")


def _check_compression_magic(path: Path) -> None:
    with open(path, "rb") as f:
        head = f.read(8)
    for magic, name in COMPRESSION_MAGIC.items():
        if head.startswith(magic):
            raise UnsupportedCompression(
                f"拒绝压缩/非 tar 格式({name})：G2 仅接受未压缩 tar-pax；如需支持须另行安全评审")


def norm_member_name(member_name: str) -> str:
    """成员名→安全相对 POSIX 路径；非法即抛 UnsafeArchive。"""
    name = member_name.replace("\\", "/")
    if "\x00" in name:
        raise UnsafeArchive("nul_byte_in_name")
    if name.startswith("//"):
        raise UnsafeArchive("unc_path")
    if name.startswith("/"):
        raise UnsafeArchive("absolute_path")
    parts = [p for p in name.split("/") if p != ""]
    if not parts:
        raise UnsafeArchive("empty_name")
    for p in parts:
        if p == "..":
            raise UnsafeArchive("parent_traversal")
        if DRIVE_RE.match(p + "/"):
            raise UnsafeArchive("drive_letter")
        base = p.rstrip(" .")  # Windows 剥尾点/空格语义
        stem = base.split(".")[0].upper()
        if stem in WINDOWS_RESERVED:
            raise UnsafeArchive(f"windows_reserved_name:{p}")
        if len(p) > MAX_COMPONENT:
            raise UnsafeArchive(f"component_too_long:{len(p)}")
    rel = "/".join(parts)
    if len(rel) > MAX_REL_PATH:
        raise UnsafeArchive(f"path_too_long:{len(rel)}")
    return rel


def inspect(tar_path: Path | str, *, max_files: int = DEFAULT_MAX_FILES,
            max_total_bytes: int = DEFAULT_MAX_TOTAL,
            max_file_bytes: int = DEFAULT_MAX_FILE,
            compression_ratio_cap: float | None = None) -> Plan:
    """只读检视；任何违例抛 UnsafeArchive 子类。compression_ratio_cap 仅用于未来压缩支持。"""
    tar_path = Path(tar_path)
    _check_compression_magic(tar_path)
    try:
        tf = tarfile.open(tar_path, "r:")  # 明确未压缩；不使用 r:*（不自动解压）
    except tarfile.CompressionError as e:
        raise UnsupportedCompression(str(e))
    except tarfile.TarError as e:
        raise CorruptArchive(f"open:{e}")
    plan = Plan(source=tar_path, root_prefix="")
    seen_exact: set[str] = set()
    seen_norm: set[str] = set()
    prefixes: set[str] = set()
    file_paths: set[str] = set()
    dir_paths: set[str] = set()
    try:
        try:
            members = tf.getmembers()
        except tarfile.TarError as e:
            raise CorruptArchive(f"read:{e}")
        for m in members:
            if m.type not in SAFE_TYPES:
                raise UnsafeArchive(f"forbidden_member_type:{m.type!r}:{m.name}")
            if m.issym() or m.islnk():
                raise UnsafeArchive(f"link_member:{m.name}")
            rel = norm_member_name(m.name)
            prefixes.add(rel.split("/")[0])
            # 父路径类型冲突：同名既是文件又是别人的父目录（a 文件 & a/b）
            parent_chain = {"/".join(rel.split("/")[:i]) for i in range(1, rel.count("/") + 1)}
            if m.isfile():
                if rel in dir_paths or rel in parent_chain & dir_paths:
                    raise UnsafeArchive(f"type_conflict:{rel}")
                file_paths.add(rel)
            else:
                if rel in file_paths:
                    raise UnsafeArchive(f"type_conflict:{rel}")
                dir_paths.add(rel)
            # 任一祖先若是文件，也冲突（a 文件已在，来了 a/b）
            for anc in parent_chain:
                if anc in file_paths:
                    raise UnsafeArchive(f"type_conflict:{anc}->/{rel}")
            if rel in seen_exact:
                raise UnsafeArchive(f"duplicate_member:{rel}")
            seen_exact.add(rel)
            norm_key = unicodedata.normalize("NFC", rel).casefold()
            if norm_key in seen_norm:
                raise UnsafeArchive(f"case_or_unicode_collision:{rel}")
            seen_norm.add(norm_key)
            if m.isfile():
                if m.size > max_file_bytes:
                    raise UnsafeArchive(f"file_too_large:{m.size}:{rel}")
                plan.total_bytes += m.size
                if plan.total_bytes > max_total_bytes:
                    raise UnsafeArchive("total_bytes_exceeds_cap")
                plan.file_count += 1
                if plan.file_count > max_files:
                    raise UnsafeArchive("too_many_files")
                plan.files.append((rel, m))
                if m.size > plan.max_file[0]:
                    plan.max_file = (m.size, rel)
            else:
                plan.dirs.append((rel, m))
    finally:
        tf.close()
    if len(prefixes) != 1:
        raise UnsafeArchive(f"multiple_or_no_root:{sorted(prefixes)}")
    plan.root_prefix = next(iter(prefixes))
    return plan


def _contained(base: Path, target: Path) -> bool:
    base = os.path.normcase(os.path.abspath(base))
    target = os.path.normcase(os.path.abspath(target))
    return target == base or target.startswith(base + os.sep)


def safe_extract(tar_path: Path | str, dest_parent: Path | str,
                 plan: Plan | None = None) -> Path:
    """解包到 dest_parent 下全新随机子目录；返回该目录。"""
    tar_path = Path(tar_path)
    dest_parent = Path(dest_parent)
    dest_parent.mkdir(parents=True, exist_ok=True)
    plan = plan or inspect(tar_path)
    free = shutil.disk_usage(dest_parent).free
    if free < plan.total_bytes * 2 + 200 * 1024 * 1024:
        raise UnsafeArchive("insufficient_disk_space")
    root = dest_parent / f"restore_{uuid.uuid4().hex[:12]}"
    if root.exists():
        raise UnsafeArchive("dest_exists")
    root.mkdir(parents=False)
    tf = tarfile.open(tar_path, "r:")
    try:
        for rel, _ in sorted(plan.dirs, key=lambda x: x[0].count("/")):
            (root / rel).mkdir(exist_ok=True)
        for rel, m in plan.files:
            out = root / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if not _contained(root, out):
                raise UnsafeArchive(f"escape_after_resolve:{rel}")
            src = tf.extractfile(m)
            if src is None:
                raise UnsafeArchive(f"no_stream:{rel}")
            copied = 0
            with open(out, "wb") as w:  # 默认权限；不 chmod/不保留 setuid/可执行位
                while copied < m.size:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    w.write(chunk)
                    copied += len(chunk)
            if copied != m.size:
                raise CorruptArchive(f"short_member:{rel}:{copied}!={m.size}")
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        tf.close()
    actual = [p for p in root.rglob("*") if p.is_file()]
    if len(actual) != plan.file_count:
        raise CorruptArchive(f"postcheck_file_count:{len(actual)}!={plan.file_count}")
    return root


# ---------------- 确定性打包 ----------------

def iter_project_files(root: Path):
    """规范化遍历：NFC、POSIX 相对路径、排序；仅普通文件/目录。"""
    root = Path(root)
    for p in sorted(root.rglob("*"), key=lambda x: unicodedata.normalize(
            "NFC", x.relative_to(root).as_posix())):
        rel = unicodedata.normalize("NFC", p.relative_to(root).as_posix())
        if p.is_symlink():
            raise UnsafeArchive(f"symlink_in_source:{rel}")
        if not (p.is_file() or p.is_dir()):
            raise UnsafeArchive(f"special_file_in_source:{rel}")
        yield rel, p


def create_archive(source_root: Path | str, out_tar: Path | str,
                   *, archive_root_name: str | None = None,
                   extra_files: dict | None = None,
                   fixed_mtime: int = 0) -> dict:
    """把 source_root 确定性打成未压缩 PAX tar。

    - 排序、mtime/uid/gid/uname/gname 固定，mode 文件 0o644/目录 0o755（不保留可执行位）
    - extra_files: {归档内相对 source root 的 posix 路径: bytes}（如 manifest.payload.json）
    - 返回 {bytes, sha256, file_count, members}；tar 关闭后由调用方做整包哈希（本函数已算）
    """
    import hashlib
    source_root = Path(source_root)
    out_tar = Path(out_tar)
    root_name = archive_root_name or source_root.name
    if not root_name or "/" in root_name or root_name in (".", ".."):
        raise UnsafeArchive(f"bad_archive_root:{root_name!r}")
    entries: list[tuple[str, int]] = []  # (posix rel under root, kind 0=file 1=virtual)
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    total = 0
    count = 0
    with tarfile.open(out_tar, "w", format=tarfile.PAX_FORMAT) as tf:
        # 根目录
        root_ti = tarfile.TarInfo(root_name)
        root_ti.type = tarfile.DIRTYPE
        root_ti.mtime = fixed_mtime
        root_ti.mode = 0o755
        root_ti.uid = root_ti.gid = 0
        root_ti.uname = root_ti.gname = ""
        tf.addfile(root_ti)

        virtual = extra_files or {}
        seen: set[str] = set()

        def add_bytes(rel: str, data: bytes):
            nonlocal total, count
            norm = unicodedata.normalize("NFC", rel.replace("\\", "/")).lstrip("./")
            if norm in seen:
                raise UnsafeArchive(f"duplicate_in_package:{norm}")
            seen.add(norm)
            ti = tarfile.TarInfo(f"{root_name}/{norm}")
            ti.size = len(data)
            ti.mtime = fixed_mtime
            ti.mode = 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            import io
            tf.addfile(ti, io.BytesIO(data))
            total += len(data)
            count += 1
            entries.append((norm, 1))

        # 实体树
        for rel, p in iter_project_files(source_root):
            arc = f"{root_name}/{rel}"
            if rel in seen:
                raise UnsafeArchive(f"duplicate_in_package:{rel}")
            seen.add(rel)
            ti = tf.gettarinfo(str(p), arcname=arc)
            ti.mtime = fixed_mtime
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            if p.is_dir():
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tf.addfile(ti)
                entries.append((rel, 2))
                continue
            if not p.is_file():
                raise UnsafeArchive(f"non_regular_source:{rel}")
            ti.type = tarfile.REGTYPE
            ti.mode = 0o644
            with open(p, "rb") as f:
                tf.addfile(ti, f)
            entries.append((rel, 0))
        # 虚拟文件（manifest）；排序后追加，名称冲突已在上方按实体集合拦截
        for rel in sorted(virtual):
            add_bytes(rel, virtual[rel])
    with open(out_tar, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            total += 0
    return {"bytes": out_tar.stat().st_size, "sha256": h.hexdigest(),
            "file_count": count, "entries": entries}


def concat_parts(part_paths: list, out_stream, *, expected_sha: str | None = None) -> dict:
    """只读流式重组 .part-XX（cat 语义）。out_stream 为调用方提供的隔离目标文件对象。"""
    import hashlib
    from .errors import HashMismatch
    h = hashlib.sha256()
    total = 0
    for pp in part_paths:
        pp = Path(pp)
        with open(pp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                out_stream.write(chunk)
                h.update(chunk)
                total += len(chunk)
    digest = h.hexdigest()
    if expected_sha and digest != expected_sha:
        raise HashMismatch(f"重组包哈希不符: {digest} != {expected_sha}")
    return {"bytes": total, "sha256": digest}
