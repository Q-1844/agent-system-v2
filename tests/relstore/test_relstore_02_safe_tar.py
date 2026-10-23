# tests/relstore/test_relstore_02_safe_tar.py
# 矩阵 3：G1 的 17 类恶意样本 + 新增长路径/类型冲突/压缩拒绝/多根/分卷重组。
import gzip
import io
import tarfile
from pathlib import Path

import pytest

from tools.relstore import safe_tar
from tools.relstore.errors import (CorruptArchive, UnsafeArchive,
                                  UnsupportedCompression)


def _ti(name, size=0, mtype=tarfile.REGTYPE, linkname=""):
    m = tarfile.TarInfo(name)
    m.size = size
    m.type = mtype
    if linkname:
        m.linkname = linkname
    return m


def build(path, members, *, gz=False):
    buf = io.BytesIO()
    tf = tarfile.open(fileobj=buf, mode="w")
    for item in members:
        if isinstance(item, tuple):
            name, data = item
            tf.addfile(_ti(name, size=len(data)), io.BytesIO(data))
        else:
            tf.addfile(item, io.BytesIO(b"") if item.isfile() else None)
    tf.close()
    raw = gzip.compress(buf.getvalue()) if gz else buf.getvalue()
    Path(path).write_bytes(raw)


def raw_header(name, size, mtype):
    def octal(v, n):
        return (b"0" if not v else format(v, "0o").encode()).rjust(n - 1, b"0") + b"\0"
    h = bytearray(512)
    nb = name.encode()
    h[0:len(nb)] = nb
    h[124:136] = octal(size, 12)
    h[136:148] = octal(0, 12)
    h[148:156] = b"        "
    h[156] = ord(mtype)
    h[257:263] = b"ustar\0"
    h[263:265] = b"00"
    chk = sum(h)
    h[148:156] = format(chk, "06o").encode() + b"\0 "
    return bytes(h)


def expect_reason(path, prefix):
    with pytest.raises((UnsafeArchive, CorruptArchive,
                        UnsupportedCompression)) as ei:
        safe_tar.inspect(path)
    assert ei.value.code.startswith(prefix) or str(ei.value).startswith(prefix), \
        f"{ei.value.code}: {ei.value}"


def test_f01_parent(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("P55/ok.txt", b"ok"), ("../../evil.txt", b"x")])
    expect_reason(p, "parent_traversal")


def test_f02_absolute(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("/tmp/evil_abs.txt", b"x")])
    expect_reason(p, "absolute_path")


def test_f03_drive_slash(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("C:/evil.txt", b"x")])
    expect_reason(p, "drive_letter")


def test_f03b_drive_backslash(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("C:\\\\evil.txt", b"x")])
    expect_reason(p, "drive_letter")


def test_f04_unc(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("//server/share/evil.txt", b"x")])
    expect_reason(p, "unc_path")


def test_f05_symlink(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("P55/ok.txt", b"ok"),
              _ti("P55/link", mtype=tarfile.SYMTYPE, linkname="../../../etc")])
    expect_reason(p, "forbidden_member_type")


def test_f06_hardlink(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("P55/ok.txt", b"ok"),
              _ti("P55/hl", mtype=tarfile.LNKTYPE, linkname="../evil")])
    expect_reason(p, "forbidden_member_type")


def test_f07_device(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [_ti("P55/chr0", mtype=tarfile.CHRTYPE)])
    expect_reason(p, "forbidden_member_type")


def test_f08_case_collision(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("P55/A/x.TXT", b"1"), ("P55/a/X.txt", b"2")])
    expect_reason(p, "case_or_unicode_collision")


def test_f09_duplicate(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("P55/x.txt", b"1"), ("P55/x.txt", b"2")])
    expect_reason(p, "duplicate_member")


def test_f10_reserved_name(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("P55/CON.txt", b"x")])
    expect_reason(p, "windows_reserved_name")


def test_f11_fake_5gib_header(tmp_path):
    p = tmp_path / "f.tar"
    p.write_bytes(raw_header("P55/fake.bin", 5 * 2 ** 30, "0") + b"\0" * 1024)
    expect_reason(p, "corrupt")


def test_f12_gzip_bomb_rejected_as_compression(tmp_path):
    p = tmp_path / "f.tar.gz"
    build(p, [("P55/z.bin", b"\0" * (5 * 1024 * 1024))], gz=True)
    expect_reason(p, "拒绝压缩")


def test_f13_multiple_roots(tmp_path):
    p = tmp_path / "f.tar"
    build(p, [("P55/a.txt", b"1"), ("OTHER/b.txt", b"2")])
    expect_reason(p, "multiple_or_no_root")


def test_f14_nul_unit():
    with pytest.raises(UnsafeArchive):
        safe_tar.norm_member_name("P55/a\x00.txt")


def test_g01_clean_passes(tmp_path):
    p = tmp_path / "g.tar"
    build(p, [("PROJ/input/s.yaml", b"x: 1\n"),
              ("PROJ/output/project.json", b'{"schema_version": 2}')])
    plan = safe_tar.inspect(p)
    assert plan.file_count == 2
    root = safe_tar.safe_extract(p, tmp_path / "out", plan)
    assert (root / "PROJ/output/project.json").read_bytes() == b'{"schema_version": 2}'


def test_f16_threshold_cap(tmp_path):
    p = tmp_path / "g.tar"
    build(p, [("PROJ/output/project.json", b'{"schema_version": 2}')])
    expect_reason(p, "file_too_large") if False else None
    with pytest.raises(UnsafeArchive, match="file_too_large"):
        safe_tar.inspect(p, max_file_bytes=10)


# ---- G2 新增 ----

def test_n17_too_long_path(tmp_path):
    p = tmp_path / "n.tar"
    longseg = "a" * 240
    build(p, [(f"PROJ/d/{longseg}.png", b"x")])
    expect_reason(p, "path_too_long")


def test_n18_type_conflict_file_then_child(tmp_path):
    p = tmp_path / "n.tar"
    build(p, [("PROJ/a", b"file-as-a"), ("PROJ/a/b.txt", b"child")])
    expect_reason(p, "type_conflict")


def test_n19_zip_magic_rejected(tmp_path):
    p = tmp_path / "n.zip"
    p.write_bytes(b"PK\x03\x04" + b"\0" * 100)
    expect_reason(p, "拒绝压缩")


def test_n20_xz_magic_rejected(tmp_path):
    p = tmp_path / "n.tar.xz"
    p.write_bytes(b"\xfd7zXZ\x00" + b"\0" * 100)
    expect_reason(p, "拒绝压缩")


def test_n21_extract_to_fresh_dir_only(tmp_path):
    p = tmp_path / "g.tar"
    build(p, [("PROJ/a.txt", b"1")])
    plan = safe_tar.inspect(p)
    r1 = safe_tar.safe_extract(p, tmp_path / "out", plan)
    r2 = safe_tar.safe_extract(p, tmp_path / "out", plan)
    assert r1 != r2  # 每次随机唯一目录，绝不覆盖既有恢复


def test_n22_parts_reassemble(tmp_path):
    p = tmp_path / "g.tar"
    payload = bytes((i * 7) % 256 for i in range(300000))
    build(p, [("PROJ/a.bin", payload)])
    data = p.read_bytes()
    parts = []
    for i, chunk in enumerate([data[:100000], data[100000:220000], data[220000:]]):
        pp = tmp_path / f"g.tar.part-{i:02d}"
        pp.write_bytes(chunk)
        parts.append(pp)
    import hashlib
    want = hashlib.sha256(data).hexdigest()
    out = tmp_path / "joined.tar"
    with open(out, "wb") as w:
        info = safe_tar.concat_parts(parts, w, expected_sha=want)
    assert info["bytes"] == len(data)
    plan = safe_tar.inspect(out)
    assert plan.file_count == 1
