# -*- coding: utf-8 -*-
"""Tavily Hub 国内代理搜索客户端（Script Writer `web.search` 第一版 Provider）。

设计约束（对应 Web Search 第一版范围）：
- 只暴露最小能力：search(query) → 压缩后的结果列表；不提供 crawl/extract。
- Provider 参数全部由 Runtime 固定（max_results=3、include_answer/raw_content/images=False），
  不开放给模型；模型只见 web.search(query)。
- 不做内部重试：timeout / 429 / 5xx / 网络失败一律映射为结构化错误 dict 返回，
  由 Agent 决定换查询词重搜、用已有知识继续，或如实告知用户（Runtime 层无无限重试）。
- 零结果是正常返回（ok=True, results=[]），不算错误。
- API Key 只从环境变量读取（.env 由 api_server.py 启动时注入 os.environ），
  绝不写入源码/日志/Git。

代理响应为双层包装：{"code":0,"message":"success","data":{"data":{…Tavily 标准体…}}}。
"""
import os
from typing import Optional

import httpx

SEARCH_ENDPOINT = "https://tavily.sharyuke.com/api/proxy/search"
API_KEY_ENV = "TAVILY_HUB_API_KEY"

# Runtime 固定的 Provider 参数（第一版不暴露给模型）
DEFAULT_MAX_RESULTS = 3
DEFAULT_DEPTH = "basic"          # probe：basic P50≈1.9s / advanced P50≈4.2s，basic 质量已足够
ADVANCED_CHUNKS_PER_SOURCE = 2   # 仅 advanced 生效（不开启 raw_content）
CONTENT_MAX_CHARS = 600          # 单条 snippet 压缩上限，控制 context 体积
DEFAULT_TIMEOUT_S = 15.0


class TavilyHubClient:
    """无状态搜索客户端。search() 返回结构化 dict，绝不抛出网络异常。"""

    def __init__(self, api_key: Optional[str] = None, timeout_s: float = DEFAULT_TIMEOUT_S):
        self._api_key = api_key or os.environ.get(API_KEY_ENV, "")
        self._timeout_s = timeout_s

    async def search(self, query: str, depth: str = DEFAULT_DEPTH) -> dict:
        """执行一次搜索。返回：
        成功 {"ok": True, "n_results": int,
              "results": [{"title","url","content","score"}],   # content 已截断
              "sources": [{"title","url"}]}                     # 轻量来源（进 meta.search_sources）
        失败 {"ok": False, "code": timeout|rate_limited|upstream_error|network_error|not_configured,
              "error": str}
        """
        if not self._api_key:
            return {"ok": False, "code": "not_configured",
                    "error": "未配置 TAVILY_HUB_API_KEY（.env / secret），搜索不可用"}
        payload = {
            "query": query,
            "search_depth": depth,
            "max_results": DEFAULT_MAX_RESULTS,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if depth == "advanced":
            payload["chunks_per_source"] = ADVANCED_CHUNKS_PER_SOURCE
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    SEARCH_ENDPOINT, json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"})
        except (httpx.TimeoutException, TimeoutError):
            return {"ok": False, "code": "timeout",
                    "error": f"搜索超时（>{int(self._timeout_s)}s）"}
        except httpx.HTTPStatusError:
            # 不会走到（下面 raise_for_status 前处理），保险分支
            return {"ok": False, "code": "upstream_error", "error": "搜索服务返回异常状态"}
        except httpx.HTTPError as e:
            return {"ok": False, "code": "network_error",
                    "error": f"网络失败：{type(e).__name__}"}
        except Exception as e:   # 兜底：任何异常都不允许让 turn 崩溃
            return {"ok": False, "code": "network_error",
                    "error": f"搜索异常：{type(e).__name__}: {str(e)[:120]}"}

        if resp.status_code == 429:
            return {"ok": False, "code": "rate_limited",
                    "error": "搜索服务限流（429），稍后或换更聚焦的查询再试"}
        if resp.status_code >= 500:
            return {"ok": False, "code": "upstream_error",
                    "error": f"搜索服务端错误（{resp.status_code}）"}
        if resp.status_code != 200:
            return {"ok": False, "code": "upstream_error",
                    "error": f"搜索服务返回 HTTP {resp.status_code}"}
        try:
            data = resp.json()
        except Exception:
            return {"ok": False, "code": "upstream_error", "error": "搜索服务返回非 JSON 内容"}

        # Hub 业务层错误（非 0 code 且无结果体），如额度耗尽：
        # {"code":42901,"message":"Credit 不足，请等待恢复或升级套餐","data":null}
        if isinstance(data, dict) and data.get("code") not in (None, 0, "0") \
                and not isinstance(data.get("data"), dict):
            code = "quota_exhausted" if data.get("code") == 42901 else "upstream_error"
            return {"ok": False, "code": code,
                    "error": f"搜索服务错误：{data.get('message') or data.get('code')}"}

        # 包装层解包：Hub 双层 {"code","message","data":{"data":{…tavily…}}}，官方单层直返。
        # 规则：只要自身还没有 "results" 而嵌套的 "data" 是 dict，就继续向内解。
        for _ in range(3):
            if isinstance(data, dict) and "results" not in data \
                    and isinstance(data.get("data"), dict):
                data = data["data"]
            else:
                break
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return {"ok": False, "code": "upstream_error", "error": "搜索服务响应结构异常"}

        results = []
        for r in data.get("results", [])[:DEFAULT_MAX_RESULTS]:
            results.append({
                "title": (r.get("title") or "")[:200],
                "url": r.get("url") or "",
                "content": (r.get("content") or "")[:CONTENT_MAX_CHARS],
                "score": r.get("score"),
            })
        return {"ok": True, "n_results": len(results), "results": results,
                "sources": [{"title": r["title"], "url": r["url"]} for r in results]}
