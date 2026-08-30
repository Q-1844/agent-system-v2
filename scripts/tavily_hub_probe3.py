# -*- coding: utf-8 -*-
"""Tavily Hub probe pass3：5 条新 basic query（质量统计）+ 1 条重复 query（缓存假设验证）。
自适应省 credit：新 query 连续 2 条空结果即中止剩余新 query。key 只从 .env 读，绝不打印。"""
import json, os, re, statistics, time, urllib.request, urllib.error
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://tavily.sharyuke.com/api/proxy/search"

def load_key():
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*TAVILY_HUB_API_KEY\s*=\s*(\S+)", line)
            if m:
                return m.group(1)
    raise SystemExit("TAVILY_HUB_API_KEY not found")

def unwrap(data):
    """修复版解包：{"code","message","data":{"data":{tavily}}} 双层 + 官方单层。
    （旧版 `"results" in data["data"]` 判断在第一层永远为假，导致 pass2/pass3 全部误读为空。）"""
    for _ in range(3):
        if isinstance(data, dict) and "results" not in data and isinstance(data.get("data"), dict):
            data = data["data"]
        else:
            break
    return data

NEW_QUERIES = [
    ("科技", "中国空间站 航天员 日常作息"),
    ("建筑/生活", "上海石库门 建筑结构 内部格局"),
    ("职业细节", "民国时期 上海 黄包车夫 收入"),
    ("文化习俗", "云南 傣族 泼水节 习俗"),
    ("刑侦/法律", "中国 派出所 报案 流程"),
]
REPEAT_QUERY = "明朝科举考试 流程"   # pass1 basic 跑过

def call(query, depth="basic"):
    payload = {"query": query, "search_depth": depth, "max_results": 3,
               "include_answer": False, "include_raw_content": False, "include_images": False}
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + load_key(), "Content-Type": "application/json"},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode("utf-8", "replace")
            ms = round((time.time() - t0) * 1000)
            data = unwrap(json.loads(body))
            results = data.get("results", [])
            return {"query": query, "depth": depth, "status": r.status, "ms": ms,
                    "hub_code": (json.loads(body).get("code") if body[:1] == "{" else None),
                    "hub_message": (json.loads(body).get("message") if body[:1] == "{" else None),
                    "n_results": len(results),
                    "chars": sum(len(x.get("content") or "") for x in results),
                    "domains": sorted({re.sub(r"^www\.", "", (x.get("url") or "").split("/")[2])
                                       for x in results if (x.get("url") or "").count("/") >= 2}),
                    "titles": [x.get("title", "")[:50] for x in results],
                    "snippet_heads": [(x.get("content") or "")[:120] for x in results]}
    except urllib.error.HTTPError as e:
        return {"query": query, "depth": depth, "status": e.code,
                "ms": round((time.time() - t0) * 1000), "error": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:
        return {"query": query, "depth": depth, "status": -1,
                "ms": round((time.time() - t0) * 1000), "error": f"{type(e).__name__}: {e}"}

def main():
    rows = []
    empty_streak = 0
    aborted = False
    for cat, q in NEW_QUERIES:
        r = call(q)
        rows.append(r)
        print(f"[new:{cat}] {r.get('status')} {r.get('ms')}ms n={r.get('n_results')} "
              f"chars={r.get('chars')} {r.get('error', '')[:80]}", flush=True)
        if r.get("status") == 200 and r.get("n_results") == 0:
            empty_streak += 1
            if empty_streak >= 2:
                aborted = True
                print("!! 连续 2 条新 query 空结果 → 中止剩余新 query（疑似额度/缓存问题）", flush=True)
                break
        else:
            empty_streak = 0
        time.sleep(2)
    r = call(REPEAT_QUERY)   # 重复 query：缓存假设验证（无论如何都跑，空结果大概率不耗额度的前提不保证，仅 1 条）
    r["is_repeat"] = True
    rows.append(r)
    print(f"[repeat] {r.get('status')} {r.get('ms')}ms n={r.get('n_results')} chars={r.get('chars')}", flush=True)
    ok = [x for x in rows if x.get("status") == 200 and x.get("n_results", 0) > 0]
    rep = {"ts": datetime.now().isoformat(), "aborted_early": aborted, "rows": rows,
           "summary": {"total": len(rows), "http_ok": sum(1 for x in rows if x.get("status") == 200),
                       "with_results": len(ok),
                       "p50_ms": statistics.median([x["ms"] for x in rows]) if rows else None,
                       "repeat_n_results": rows[-1].get("n_results"),
                       "domains_seen": sorted({d for x in ok for d in x.get("domains", [])})}}
    out = os.path.join(ROOT, "scripts", "tavily_hub_probe3_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print("\nreport:", out)
    print(json.dumps(rep["summary"], ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
