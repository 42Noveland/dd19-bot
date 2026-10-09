"""网页正文抓取（Firecrawl Scrape API）。

供聊天工具 fetch_url 使用：群友发了链接、模型想了解内容时抓取正文
（markdown 轻量转纯文本、按上限截断）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 qqbot/1.0"


class FetchError(RuntimeError):
    """抓取失败（网络/接口/内容为空）。"""


@dataclass
class WebPage:
    url: str
    title: str
    text: str


def _plain(md: str) -> str:
    """轻量 markdown → 纯文本：去代码围栏/图片/链接语法，压缩空行。"""
    t = str(md or "")
    t = re.sub(r"```[^\n]*\n?", "", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)  # 图片
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # 链接→文字
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


async def fetch_page(
    url: str,
    *,
    api_key: str,
    base_url: str = "https://api.firecrawl.dev",
    timeout: float = 30.0,
    max_chars: int = 1800,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebPage:
    """抓取网页正文（Firecrawl /v2/scrape）。"""
    if not api_key:
        raise FetchError("未配置抓取 API key")
    target = str(url or "").strip()
    if not target.startswith(("http://", "https://")):
        raise FetchError("不是有效的链接")
    api = base_url.rstrip("/") + "/v2/scrape"
    payload = {"url": target, "formats": ["markdown"], "onlyMainContent": True}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        resp = await client.post(api, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    if not data.get("success", True):
        raise FetchError(str(data.get("error") or "抓取接口返回失败"))
    d = data.get("data") or {}
    if isinstance(d, list):  # 个别版本返回列表
        d = d[0] if d else {}
    if not isinstance(d, dict):
        raise FetchError("抓取接口返回格式异常")
    text = _plain(str(d.get("markdown") or d.get("content") or d.get("text") or ""))
    if not text:
        raise FetchError("页面没有可读正文")
    title = str((d.get("metadata") or {}).get("title") or "").strip()
    return WebPage(url=target, title=title, text=text[:max_chars])
