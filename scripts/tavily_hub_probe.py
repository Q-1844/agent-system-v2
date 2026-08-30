# -*- coding: utf-8 -*-
"""Tavily Hub 国内代理 probe：只读 .env 中的 TAVILY_HUB_API_KEY，绝不打印 key。"""
import json, os, re, statistics, time, urllib.request, urllib.error
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_key():
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*TAVILY_HUB_API_KEY\s*=\s*(\S+)", line)
            if m:
                return m.group(1)
    raise SystemExit("TAVILY_HUB_API_KEY not found in .env")

URL = "https://tavily.sharyuke.com/api/proxy/search"

QUERIES = [
    # (类别, 查询)
    ("历史背景", "1940年代上海租界 生活状况"),
    ("历史背景", "明朝科举考试 流程"),
    ("城市环境", "重庆火锅店 市井生活"),
    ("医疗流程", "急诊室抢救流程 心肺复苏"),
    ("医疗流程", "中国医院 ICU 探视制度"),
    ("刑侦/法律", "中国刑事警察 办案流程"),
    ("职业细节", "外卖骑手 一天工作流程"),
    ("文化习俗", "潮汕 功夫茶 礼仪"),
]

ADV_QUERIES = [
    ("科技", "中国高铁 CR400AF 技术参数"),
    ("建筑/生活场景", "老北京四合院 建筑格局"),
    ("刑侦/法律", "中国法庭 庭审流程 顺序"),
    ("历史背景", "宋代夜市 商业繁荣"),
    ("职业细节", "远洋货轮 船员 等级职责"),
    ("医疗流程", "法医尸检 标准程序"),
]

def call(query, depth, extra=None):
    payload = {
        "query": query,
        "search_depth": depth,
        "max_results": 3,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    if depth == "advanced":
        payload["chunks_per_source"] = 2
    if extra:
        payload.update(extra)
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + load_key(),
                 "Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode("utf-8", "replace")
            ms = round((time.time() - t0) * 1000)
            data = json.loads(body)
            # 代理双层包装: {"code":0,"message":"success","data":{"data":{tavily}}}
            if isinstance(data, dict) and isinstance(data.get("data"), dict) and "results" in data["data"]:
                data = data["data"]
            if isinstance(data, dict) and isinstance(data.get("data"), dict) and "results" in data["data"]:
                data = data["data"]
            results = data.get("results", [])
            return {
                "query": query, "depth": depth, "status": r.status, "ms": ms,
                "n_results": len(results),
                "chars": len(json.dumps(results, ensure_ascii=False)),
                "domains": [re.sub(r"^www\.", "", (x.get("url") or "").split("/")[2]) for x in results if x.get("url")],
                "titles": [x.get("title", "")[:60] for x in results],
                "snippets": [x.get("content", "") for x in results],
                "response_time_field": data.get("response_time"),
            }
    except urllib.error.HTTPError as e:
        return {"query": query, "depth": depth, "status": e.code,
                "ms": round((time.time() - t0) * 1000), "error": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return {"query": query, "depth": depth, "status": -1,
                "ms": round((time.time() - t0) * 1000), "error": f"{type(e).__name__}: {e}"}

def summarize(rows):
    ok = [r for r in rows if r["status"] == 200]
    lat = sorted(r["ms"] for r in ok)
    def pct(p):
        if not lat: return None
        i = max(0, min(len(lat) - 1, int(round(p / 100 * (len(lat) - 1)))))
        return lat[i]
    return {
        "total": len(rows), "success": len(ok),
        "success_rate": round(len(ok) / len(rows), 3) if rows else None,
        "errors": [{"q": r["query"], "status": r["status"], "err": r.get("error", "")[:120]} for r in rows if r["status"] != 200],
        "p50_ms": pct(50), "p95_ms": pct(95), "max_ms": max(lat) if lat else None,
        "avg_results": round(statistics.mean([r["n_results"] for r in ok]), 2) if ok else None,
        "avg_chars": round(statistics.mean([r["chars"] for r in ok])) if ok else None,
        "domains": sorted({d for r in ok for d in r["domains"]}),
    }

def main():
    rows = []
    for cat, q in QUERIES:
        print(f"[basic] {cat}: {q}", flush=True)
        r = call(q, "basic"); rows.append(r); print("  ->", r["status"], r.get("ms"), "ms", r.get("n_results"), "results", r.get("error", "")[:100], flush=True)
        time.sleep(1.5)
    for cat, q in ADV_QUERIES:
        print(f"[advanced] {cat}: {q}", flush=True)
        r = call(q, "advanced"); rows.append(r); print("  ->", r["status"], r.get("ms"), "ms", r.get("n_results"), "results", r.get("error", "")[:100], flush=True)
        time.sleep(1.5)
    report = {
        "ts": datetime.now().isoformat(),
        "endpoint": URL,
        "basic": summarize([r for r in rows if r["depth"] == "basic"]),
        "advanced": summarize([r for r in rows if r["depth"] == "advanced"]),
        "details": [{k: v for k, v in r.items() if k != "snippets"} for r in rows],
        "snippets": [{ "q": r["query"], "d": r["depth"], "snips": r.get("snippets", [])} for r in rows if r["status"] == 200],
    }
    out = os.path.join(ROOT, "scripts", "tavily_hub_probe_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps({"basic": report["basic"], "advanced": report["advanced"]}, ensure_ascii=False, indent=2))
    print("report:", out)

if __name__ == "__main__":
    main()
