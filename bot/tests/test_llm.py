import asyncio
import json

import httpx
import pytest

from core import config as core_config
from core import llm
from core.config import load_config


@pytest.fixture(autouse=True)
def _hermetic_config(monkeypatch):
    """用例级隔离：chat_once 使用进程级单例 get_config()，这里固定为空配置，
    避免读取真实 bot/.env（其中含人设/密钥）导致断言不确定。"""
    monkeypatch.setattr(core_config, "_config", core_config.load_config({}), raising=False)


def test_rate_limit():
    llm.reset_rate_limit()
    assert llm.allow(1, 1, now=0.0, cooldown=5.0) is True
    assert llm.allow(1, 1, now=1.0, cooldown=5.0) is False
    assert llm.allow(1, 1, now=5.1, cooldown=5.0) is True
    assert llm.allow(1, 2, now=5.2, cooldown=5.0) is True  # 另一个用户不受影响


def _capture_transport(captured: dict, message: dict, finish_reason: str = "stop"):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": finish_reason, "message": message}]},
        )

    return httpx.MockTransport(handler)


def test_deepseek_request_sends_thinking_payload():
    captured = {}
    provider = load_config({"DEEPSEEK_API_KEY": "dk-test"}).providers["deepseek"]
    completion = asyncio.run(
        llm.chat_once("hi", provider, transport=_capture_transport(captured, {"content": " 你好呀 "}))
    )
    assert completion.text == "你好呀"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer dk-test"
    assert captured["body"]["model"] == "deepseek-flash"
    assert captured["body"]["messages"][0]["content"] == "hi"
    assert captured["body"]["thinking"] == {"type": "enabled"}       # 思考默认开
    assert captured["body"]["reasoning_effort"] == "high"            # 默认档位


def test_completion_extracts_reasoning_field():
    captured = {}
    provider = load_config({}).providers["local"]
    completion = asyncio.run(
        llm.chat_once(
            "hi",
            provider,
            transport=_capture_transport(
                captured,
                {"content": "答案在这里", "reasoning_content": "先想一下再回答"},
            ),
        )
    )
    assert completion.text == "答案在这里"
    assert completion.reasoning == "先想一下再回答"
    assert completion.truncated is False


def test_completion_extracts_inline_think_and_marks_truncated():
    captured = {}
    provider = load_config({}).providers["local"]
    completion = asyncio.run(
        llm.chat_once(
            "hi",
            provider,
            transport=_capture_transport(
                captured,
                {"content": "<think>内联思考内容</think>最终回答"},
                finish_reason="length",
            ),
        )
    )
    assert completion.text == "最终回答"
    assert completion.reasoning == "内联思考内容"
    assert completion.truncated is True


def test_empty_content_raises():
    captured = {}
    provider = load_config({}).providers["local"]
    with pytest.raises(llm.EmptyReplyError):
        asyncio.run(
            llm.chat_once(
                "hi",
                provider,
                transport=_capture_transport(
                    captured,
                    {"content": "", "reasoning_content": "只有思考没有正文"},
                    finish_reason="length",
                ),
            )
        )


def test_opencode_request_sends_effort_and_session():
    captured = {}
    provider = load_config({"OPENCODE_GO_API_KEY": "ok-test"}).providers["opencode_go"]
    asyncio.run(
        llm.chat_once(
            "hi",
            provider,
            session_key="qqbot-group-111",
            transport=_capture_transport(captured, {"content": "好"}),
        )
    )
    assert captured["headers"]["x-opencode-session"] == "qqbot-group-111"
    assert captured["headers"]["authorization"] == "Bearer ok-test"
    assert captured["body"]["model"] == "deepseek-v4.1-flash"
    assert captured["body"]["reasoning_effort"] == "high"   # 中转按此开思考
    assert "thinking" not in captured["body"]               # 与 Hermes opencode-go 插件口径一致：单发 effort

    # 未提供 session_key 时兜底稳定值，避免 400 MissingSessionID 静默降级
    captured2 = {}
    asyncio.run(
        llm.chat_once("hi", provider, transport=_capture_transport(captured2, {"content": "好"}))
    )
    assert captured2["headers"]["x-opencode-session"] == "qqbot-default"


def test_system_prompt_injected_when_configured(monkeypatch):
    captured = {}
    cfg = load_config({"DEEPSEEK_API_KEY": "dk", "LLM_SYSTEM_PROMPT": "你是猫娘\\n回答简短"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    provider = cfg.providers["deepseek"]
    asyncio.run(llm.chat_once("hi", provider, transport=_capture_transport(captured, {"content": "喵"})))
    messages = captured["body"]["messages"]
    assert messages[0] == {"role": "system", "content": "你是猫娘\n回答简短"}
    assert messages[1] == {"role": "user", "content": "hi"}


def test_no_system_prompt_by_default():
    captured = {}
    provider = load_config({}).providers["local"]
    asyncio.run(llm.chat_once("hi", provider, transport=_capture_transport(captured, {"content": "ok"})))
    assert all(m["role"] != "system" for m in captured["body"]["messages"])


def test_chat_falls_back_until_success(monkeypatch):
    calls: list[str] = []

    async def fake_chat_once(text, provider, **kwargs):
        calls.append(provider.name)
        if provider.name == "local":
            raise httpx.ConnectError("llama not running")
        return llm.Completion(text="备用成功", reasoning="想了想")

    monkeypatch.setattr(llm, "chat_once", fake_chat_once)
    reply = asyncio.run(llm.chat("hi", chain=["local", "deepseek"]))
    assert calls == ["local", "deepseek"]
    assert reply.provider == "deepseek"
    assert reply.text == "备用成功"
    assert reply.reasoning == "想了想"
