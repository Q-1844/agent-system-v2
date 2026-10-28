#!/usr/bin/env python3
"""生成「本地改动 vs 原始 ZIP」的精确 diff（报告附件）。

从原始 ZIP 直接取回未改动的 3 个文件，与本次本地修改后的文件逐行对比。
"""
from __future__ import annotations

import difflib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "local_60s_evidence"
ZIP = Path(r"C:\Users\曹文淇\Desktop\ai工作流\测试25\源码\A仓_阶段三_C2镜头级智能并行_完整源码与测试.zip")
PREFIX = "A仓_阶段二三_C2全片智能并行_66b81092/"
TARGETS = ["src/core/image_host.py", "routers/shots.py", "src/ai/autopilot.py"]


def main() -> int:
    chunks = []
    with zipfile.ZipFile(ZIP) as z:
        for t in TARGETS:
            orig = z.read(PREFIX + t).decode("utf-8").splitlines(keepends=True)
            cur = (ROOT / t).read_text(encoding="utf-8").splitlines(keepends=True)
            d = list(difflib.unified_diff(orig, cur, fromfile=f"a/{t} (ZIP 原始)",
                                         tofile=f"b/{t} (本地修改后)", n=3))
            chunks.append("".join(d))
    diff = "".join(chunks)
    (OUT / "local_modifications.diff").write_text(diff, encoding="utf-8")
    changed = [l for l in diff.splitlines() if l.startswith(("+", "-"))
               and not l.startswith(("+++", "---"))]
    print(f"diff written: {OUT / 'local_modifications.diff'}")
    print(f"changed lines: {len(changed)}")
    for l in changed:
        print("  " + l)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
