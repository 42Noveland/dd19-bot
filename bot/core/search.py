"""Web 搜索（Firecrawl Search API）。

用于 /search 指令与"@我 搜索 xxx"前缀路由；结果供 LLM 总结或直接列链接。
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 qqbot/1.0"


class SearchError(RuntimeError):
    """搜索失败（网络/接口错误）。"""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


async def web_search(
    query: str,
    *,
    api_key: str,
    base_url: str = "https://api.firecrawl.dev",
    limit: int = 5,
    timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[SearchResult]:
    """调用 Firecrawl /v2/search，返回前 limit 条结果。"""
    if not api_key:
        raise SearchError("未配置搜索 API key")
    url = base_url.rstrip("/") + "/v2/search"
    payload = {"query": query, "limit": limit}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    if not data.get("success", True):
        raise SearchError(str(data.get("error") or "搜索接口返回失败"))
    raw = data.get("data")
    items = None
    if isinstance(raw, dict):
        items = raw.get("web") or raw.get("news") or []
    elif isinstance(raw, list):  # v1 兼容形态
        items = raw
    results: list[SearchResult] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result = SearchResult(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("url") or "").strip(),
            snippet=str(item.get("description") or item.get("snippet") or "").strip(),
        )
        if result.url:
            results.append(result)
    return results[:limit]
