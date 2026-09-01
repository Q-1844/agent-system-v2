# -*- coding: utf-8 -*-
"""Golden Path 资产落盘核验：character views / shot images / videos。
用法: python _gp_verify_assets.py <project> [views|images|videos|all]
"""
import json, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def load(project):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "projects", project, "output", "project.json")
    return json.load(open(p, encoding="utf-8")), os.path.dirname(p)

def norm(fp):
    return fp.replace("\\", "/")

def verify_views(project):
    d, base = load(project)
    print(f"=== character views 落盘核验 ===")
    tot = ok = 0
    for ch in d.get("characters", []) or []:
        for k, v in (ch.get("views") or {}).items():
            if not isinstance(v, dict): continue
            tot += 1
            fp = norm(v.get("local") or "")
            full = os.path.join(base, fp.split("output/", 1)[-1]) if "output/" in fp else os.path.join(base, fp)
            sz = os.path.getsize(full) if os.path.exists(full) else -1
            url = v.get("url")
            mark = "OK" if sz > 1000 else "MISSING/SMALL"
            print(f"  {mark:12} {ch.get('name')}/{k:5} local_sz={sz:>8} url={'Y' if url else 'N'} status={v.get('status')}")
            if sz > 1000: ok += 1
    print(f"  --> {ok}/{tot} views files exist >1KB\n")
    return ok, tot

def verify_images(project):
    d, base = load(project)
    print(f"=== shot 首帧图片落盘核验（first_frame.candidates + batches.candidates） ===")
    tot = ok = 0; tot_sz = 0
    for sc in d.get("scenes", []) or []:
        for sh in sc.get("shots", []) or []:
            sid = sh.get("shot_id")
            shot_ok = 0; cands_seen = 0
            ff = sh.get("first_frame") or {}
            if isinstance(ff, dict):
                for c in ff.get("candidates", []) or []:
                    if not isinstance(c, dict): continue
                    cands_seen += 1
                    fp = c.get("local")
                    full = os.path.join(base, norm(fp).split("output/", 1)[-1]) if fp and "output/" in norm(fp) else (os.path.join(base, norm(fp)) if fp else None)
                    sz = os.path.getsize(full) if full and os.path.exists(full) else -1
                    tot += 1
                    if sz > 1000: ok += 1; shot_ok += 1; tot_sz += sz
            for b in sh.get("first_frame_batches", []) or []:
                for c in b.get("candidates", []) or []:
                    if not isinstance(c, dict): continue
                    cands_seen += 1
                    fp = c.get("local")
                    full = os.path.join(base, norm(fp).split("output/", 1)[-1]) if fp and "output/" in norm(fp) else (os.path.join(base, norm(fp)) if fp else None)
                    sz = os.path.getsize(full) if full and os.path.exists(full) else -1
                    tot += 1
                    if sz > 1000: ok += 1; shot_ok += 1; tot_sz += sz
            sel = sh.get("selected_first_frame")
            print(f"  {sid:9} candidates={cands_seen} files_ok={shot_ok} selected={sel}")
    print(f"  --> {ok}/{tot} shot image files exist >1KB, total {tot_sz/1024/1024:.1f}MB\n")
    return ok, tot

def verify_videos(project):
    d, base = load(project)
    print(f"=== shot 视频落盘核验 ===")
    tot = ok = 0
    for sc in d.get("scenes", []) or []:
        for sh in sc.get("shots", []) or []:
            fv = sh.get("final_video")
            fv = fv if isinstance(fv, dict) else {}
            fp = fv.get("local") or fv.get("url")
            if fp and fv.get("status") == "done":
                tot += 1
                full = os.path.join(base, norm(fp).split("output/", 1)[-1]) if "output/" in norm(fp) and not fp.startswith("http") else (fp if fp.startswith("http") else os.path.join(base, norm(fp)))
                if fp.startswith("http"):
                    mark = "URL" ; ok += 1
                else:
                    sz = os.path.getsize(full) if os.path.exists(full) else -1
                    mark = "OK" if sz > 5000 else "MISSING/SMALL"
                    if sz > 5000: ok += 1
                print(f"  {mark:12} {sh.get('shot_id')} final_video status={fv.get('status')} sz={'(url)' if fp.startswith('http') else os.path.getsize(full) if os.path.exists(full) else -1}")
    print(f"  --> {ok}/{tot} video files exist\n")
    return ok, tot

if __name__ == "__main__":
    project = sys.argv[1]
    what = sys.argv[2] if len(sys.argv) > 2 else "all"
    if what in ("views", "all"): verify_views(project)
    if what in ("images", "all"): verify_images(project)
    if what in ("videos", "all"): verify_videos(project)
