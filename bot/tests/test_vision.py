import asyncio
import hashlib
import json

import httpx

from core import context, vision
from core.config import load_config


def _setup(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(vision, "_IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(vision.budget, "exhausted", lambda *a, **k: False)
    recorded = []
    monkeypatch.setattr(vision.budget, "record", lambda name, tokens, **k: recorded.append((name, tokens)))
    return recorded


def test_describe_cloud_then_cache(tmp_path, monkeypatch):
    recorded = _setup(tmp_path, monkeypatch)
    payload = b"\x89PNG" + b"x" * 64

    async def fake_download(url, timeout):
        return payload

    monkeypatch.setattr(vision, "_download", fake_download)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["model"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " 一只测试猫 "}}], "usage": {"total_tokens": 55}},
        )

    monkeypatch.setattr(vision, "_transport", httpx.MockTransport(handler))
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok"})
    out = asyncio.run(vision.describe("http://img/1.png", cfg))
    assert out == "一只测试猫"
    assert seen == ["deepseek-v4-flash-vision-exp"]
    assert recorded == [("vision", 55)]
    # 同一张图再来：走 md5 缓存，零模型调用
    out2 = asyncio.run(vision.describe("http://img/1.png", cfg))
    assert out2 == "一只测试猫"
    assert len(seen) == 1
    md5 = hashlib.md5(payload).hexdigest()
    assert context.image_get(md5)["seen_count"] == 2
    assert (tmp_path / "images" / f"{md5}.png").exists()


def test_describe_cloud_fail_falls_back_local(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    calls = []

    async def fake_download(url, timeout):
        return b"\xff\xd8\xff" + b"y" * 32  # jpeg 魔数

    monkeypatch.setattr(vision, "_download", fake_download)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "opencode.ai" in str(request.url):
            return httpx.Response(500, text="cloud down")
        return httpx.Response(200, json={"choices": [{"message": {"content": "本地猫"}}]})

    monkeypatch.setattr(vision, "_transport", httpx.MockTransport(handler))
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok"})
    out = asyncio.run(vision.describe("http://img/2.png", cfg))
    assert out == "本地猫"
    assert any("opencode.ai" in u for u in calls) and any("8082" in u for u in calls)


def test_describe_disabled_or_bad_url(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cfg_off = load_config({"VISION_ENABLED": "0"})
    assert asyncio.run(vision.describe("http://x/1.png", cfg_off)) == ""

    async def boom(url, timeout):
        raise httpx.ConnectError("expired")

    monkeypatch.setattr(vision, "_download", boom)
    cfg = load_config({})
    assert asyncio.run(vision.describe("http://x/2.png", cfg)) == ""


def test_describe_prefers_local_file(tmp_path, monkeypatch):
    """有本地缓存文件时不得走 URL 下载（QQ CDN 链接会过期）。"""
    _setup(tmp_path, monkeypatch)
    payload = b"\x89PNG" + b"z" * 64
    f = tmp_path / "cached.png"
    f.write_bytes(payload)

    async def boom(url, timeout):
        raise AssertionError("不应该走 URL 下载")

    monkeypatch.setattr(vision, "_download", boom)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "本地缓存猫"}}]})

    monkeypatch.setattr(vision, "_transport", httpx.MockTransport(handler))
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok"})
    out = asyncio.run(vision.describe("http://img/dead.png", cfg, local_path=str(f)))
    assert out == "本地缓存猫"
