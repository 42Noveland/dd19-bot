"""core.webfetch 单元测试：Firecrawl scrape 解析与错误分支（全部走 MockTransport）。"""
import asyncio

import httpx

from core import webfetch


def _transport(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def test_parse_markdown_and_title():
    payload = {
        "success": True,
        "data": {
            "markdown": "# 标题\n\n正文[链接](http://x)内容\n```code```",
            "metadata": {"title": "T"},
        },
    }
    page = asyncio.run(webfetch.fetch_page("https://a.com", api_key="k", transport=_transport(payload)))
    assert page.title == "T"
    assert "正文链接内容" in page.text  # 链接语法还原为文字
    assert "code" not in page.text  # 代码围栏剔除


def test_success_false_raises():
    try:
        asyncio.run(
            webfetch.fetch_page(
                "https://a.com", api_key="k", transport=_transport({"success": False, "error": "bad"})
            )
        )
    except webfetch.FetchError as exc:
        assert "bad" in str(exc)
    else:
        raise AssertionError("should raise FetchError")


def test_bad_url_and_empty_raise():
    try:
        asyncio.run(webfetch.fetch_page("not-a-url", api_key="k"))
    except webfetch.FetchError:
        pass
    else:
        raise AssertionError("should raise FetchError")
    try:
        asyncio.run(
            webfetch.fetch_page(
                "https://a.com", api_key="k", transport=_transport({"success": True, "data": {"markdown": ""}})
            )
        )
    except webfetch.FetchError:
        pass
    else:
        raise AssertionError("should raise FetchError")


def test_missing_key_raises():
    try:
        asyncio.run(webfetch.fetch_page("https://a.com", api_key=""))
    except webfetch.FetchError:
        pass
    else:
        raise AssertionError("should raise FetchError")
