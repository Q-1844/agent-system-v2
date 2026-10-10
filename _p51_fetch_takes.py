# -*- coding: utf-8 -*-
"""Phase51 C：全版本拉取——把每镜所有批次的视频版本从 provider URL 下载回本地
（版本 local 路径别名覆盖写，历史批次的字节只在 provider 上）。
供看片页「全部批次陈列」：用户要看所有废片/落选版本（10-10 用户指令）。"""
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = Path("_p51_c_review/takes")
OUT.mkdir(parents=True, exist_ok=True)


def fetch_all():
    from src.core.store import ProjectStore
    p = ProjectStore(Path("projects/P50_B")).load()
    todo = []
    for sc in p.scenes:
        for sh in sc.shots:
            for vv in (sh.video_versions or []):
                if not vv.url:
                    continue
                dest = OUT / sh.shot_id / f"{vv.version_id}.mp4"
                if dest.exists() and dest.stat().st_size > 10000:
                    continue
                todo.append((sh.shot_id, vv.version_id, vv.url, dest))
    print(f"to fetch: {len(todo)}", flush=True)
    ok = fail = 0
    for i, (sid, vid, url, dest) in enumerate(todo):
        dest.parent.mkdir(parents=True, exist_ok=True)
        got = False
        for attempt in range(4):
            try:
                urllib.request.urlretrieve(url, dest)
                got = True
                break
            except Exception as e:
                wait = 4 * (attempt + 1)
                print(f"  retry {sid}/{vid[:14]} ({type(e).__name__}) wait {wait}s", flush=True)
                time.sleep(wait)
        if got:
            ok += 1
        else:
            fail += 1
            print(f"  FAILED {sid}/{vid}", flush=True)
        if (i + 1) % 10 == 0:
            print(f"  progress {i+1}/{len(todo)} ok={ok} fail={fail}", flush=True)
    print(f"DONE ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    fetch_all()
