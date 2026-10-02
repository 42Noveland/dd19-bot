"""core.search 单元测试：Firecrawl 响应解析与错误分支（全部走 MockTransport）。"""
import asyncio

import httpx

from core import search


def _transport(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def test_parse_v2_shape():
    payload = {
        "success": True,
        "data": {
            "web": [
                {"title": "T1", "url": "http://a", "description": "D1"},
                {"title": "T2", "url": "http://b", "description": "D2"},
            ]
        },
    }
    results = asyncio.run(search.web_search("q", api_key="k", transport=_transport(payload)))
    assert len(results) == 2
    assert results[0].title == "T1"
    assert results[0].url == "http://a"
    assert results[0].snippet == "D1"


def test_success_false_raises():
    payload = {"success": False, "error": "bad key"}
    try:
        asyncio.run(search.web_search("q", api_key="k", transport=_transport(payload)))
    except search.SearchError as exc:
        assert "bad key" in str(exc)
    else:
        raise AssertionError("should raise SearchError")


def test_missing_key_raises():
    try:
        asyncio.run(search.web_search("q", api_key=""))
    except search.SearchError:
        pass
    else:
        raise AssertionError("should raise SearchError")


def test_empty_results_ok():
    payload = {"success": True, "data": {"web": []}}
    assert asyncio.run(search.web_search("q", api_key="k", transport=_transport(payload))) == []
