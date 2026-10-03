import asyncio
import json

import httpx
import pytest

from core import budget as core_budget
from core import config as core_config
from core import llm
from core import search as core_search
from core.config import load_config


@pytest.fixture(autouse=True)
def _hermetic_config(monkeypatch):
    """用例级隔离：chat_once 使用进程级单例 get_config()，这里固定为空配置，
    并把预算记账/额度判断替换为确定性桩，避免读写真实 bot/.env 与用量文件。"""
    monkeypatch.setattr(core_config, "_config", core_config.load_config({}), raising=False)
    monkeypatch.setattr(core_budget, "record", lambda *a, **k: None)
    monkeypatch.setattr(core_budget, "exhausted", lambda *a, **k: False)
    monkeypatch.setattr(core_budget, "remaining", lambda *a, **k: 10**9)


def test_rate_limit():
    llm.reset_rate_limit()
    assert llm.allow(1, 1, now=0.0, cooldown=5.0) is True
    assert llm.allow(1, 1, now=1.0, cooldown=5.0) is False
    assert llm.allow(1, 1, now=5.1, cooldown=5.0) is True
    assert llm.allow(1, 2, now=5.2, cooldown=5.0) is True  # 另一个用户不受影响


def _capture_transport(captured: dict, message: dict, finish_reason: str = "stop", usage: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        body: dict = {"choices": [{"finish_reason": finish_reason, "message": message}]}
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


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
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "LLM_SYSTEM_PROMPT": "你是测试人设\\n回答简短"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    provider = cfg.providers["opencode_go"]
    asyncio.run(llm.chat_once("hi", provider, transport=_capture_transport(captured, {"content": "好"})))
    messages = captured["body"]["messages"]
    assert messages[0] == {"role": "system", "content": "你是测试人设\n回答简短"}
    assert messages[1] == {"role": "user", "content": "hi"}


def test_no_system_prompt_by_default():
    captured = {}
    provider = load_config({}).providers["local"]
    asyncio.run(llm.chat_once("hi", provider, transport=_capture_transport(captured, {"content": "ok"})))
    assert all(m["role"] != "system" for m in captured["body"]["messages"])


def test_usage_recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(core_budget, "record", lambda name, tokens, **k: calls.append((name, tokens)))
    captured = {}
    provider = load_config({"OPENCODE_GO_API_KEY": "ok"}).providers["opencode_go"]
    asyncio.run(
        llm.chat_once(
            "hi", provider, transport=_capture_transport(captured, {"content": "好"}, usage={"total_tokens": 123})
        )
    )
    assert calls == [("opencode_go", 123)]


def test_quota_exceeded_raises_and_chain_not_fallback(monkeypatch):
    monkeypatch.setattr(core_budget, "exhausted", lambda *a, **k: True)
    provider = load_config({}).providers["local"]
    with pytest.raises(llm.QuotaExceededError):
        asyncio.run(llm.chat_once("hi", provider, transport=_capture_transport({}, {"content": "x"})))
    with pytest.raises(llm.QuotaExceededError):
        asyncio.run(llm.chat("hi", chain=["local", "opencode_go"]))


def test_max_tokens_respects_call_limit(monkeypatch):
    captured = {}
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "LLM_MAX_TOKENS": "100000"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    asyncio.run(
        llm.chat_once("hi", cfg.providers["opencode_go"], transport=_capture_transport(captured, {"content": "ok"}))
    )
    expected = max(64, 100000 - core_budget.estimate_tokens("hi"))
    assert captured["body"]["max_tokens"] == expected


def test_input_truncated_to_call_limit(monkeypatch):
    captured = {}
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "LLM_MAX_TOKENS": "1000"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    asyncio.run(
        llm.chat_once("啊" * 5000, cfg.providers["opencode_go"], transport=_capture_transport(captured, {"content": "ok"}))
    )
    sent = captured["body"]["messages"][-1]["content"]
    assert "内容过长已截断" in sent
    assert len(sent) < 5000


def test_tools_payload_when_enabled(monkeypatch):
    captured = {}
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "SEARCH_API_KEY": "fk"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    asyncio.run(
        llm.chat_once("hi", cfg.providers["opencode_go"], transport=_capture_transport(captured, {"content": "ok"}))
    )
    assert captured["body"]["tools"][0]["function"]["name"] == "web_search"


def test_tools_absent_without_search_key(monkeypatch):
    captured = {}
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    asyncio.run(
        llm.chat_once("hi", cfg.providers["opencode_go"], transport=_capture_transport(captured, {"content": "ok"}))
    )
    assert "tools" not in captured["body"]


def test_tools_absent_for_local_by_default(monkeypatch):
    captured = {}
    cfg = load_config({"SEARCH_API_KEY": "fk"})  # local 默认 LLM_LOCAL_TOOLS=off
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    asyncio.run(
        llm.chat_once("hi", cfg.providers["local"], transport=_capture_transport(captured, {"content": "ok"}))
    )
    assert "tools" not in captured["body"]


def test_tool_loop_executes_search_and_returns_final(monkeypatch):
    searched = {}

    async def fake_search(query, **kwargs):
        searched["query"] = query
        return [core_search.SearchResult(title="天气页", url="http://x", snippet="晴 15-24℃")]

    monkeypatch.setattr(core_search, "web_search", fake_search)
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "SEARCH_API_KEY": "fk"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {"name": "web_search", "arguments": json.dumps({"query": "北京天气"})},
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"total_tokens": 30},
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "晴，15-24℃"}}], "usage": {"total_tokens": 20}},
        )

    completion = asyncio.run(
        llm.chat_once("北京今天天气", cfg.providers["opencode_go"], transport=httpx.MockTransport(handler))
    )
    assert completion.text == "晴，15-24℃"
    assert completion.total_tokens == 50
    assert searched["query"] == "北京天气"
    second_messages = requests[1]["messages"]
    assert any(m.get("role") == "tool" and "晴 15-24℃" in str(m.get("content")) for m in second_messages)
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second_messages)


def test_tool_loop_responds_to_every_tool_call(monkeypatch):
    """一轮内多个 tool_call：每个都必须回填 tool 消息（否则下一轮请求 400）。"""
    queries: list[str] = []

    async def fake_search(query, **kwargs):
        queries.append(query)
        return [core_search.SearchResult(title="T", url="http://x", snippet=f"结果:{query}")]

    monkeypatch.setattr(core_search, "web_search", fake_search)
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "SEARCH_API_KEY": "fk"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            calls = [
                {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": "q1"})}},
                {"id": "c2", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": "q2"})}},
                {"id": "c3", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": "q3"})}},
            ]
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": calls}}]}
            )
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "汇总好了"}}]})

    completion = asyncio.run(
        llm.chat_once("汇总三条", cfg.providers["opencode_go"], transport=httpx.MockTransport(handler))
    )
    assert completion.text == "汇总好了"
    assert queries == ["q1", "q2"]  # 实际只执行前 2 个搜索
    tool_msgs = [m for m in requests[1]["messages"] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2", "c3"]  # 3 个调用全部有回填


def test_tool_loop_force_final_round_strips_tools(monkeypatch):
    """模型每轮都还想搜索：最后强制一轮不带工具，逼出最终回答，而不是报轮次超限。"""

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr(core_search, "web_search", fake_search)
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "SEARCH_API_KEY": "fk", "LLM_TOOL_MAX_ROUNDS": "2"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    seen_tools: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_tools.append("tools" in body)
        if "tools" in body:
            calls = [
                {
                    "id": f"c{len(seen_tools)}",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": json.dumps({"query": "x"})},
                }
            ]
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": calls}}]}
            )
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "最终汇总"}}]})

    completion = asyncio.run(
        llm.chat_once("汇总", cfg.providers["opencode_go"], transport=httpx.MockTransport(handler))
    )
    assert completion.text == "最终汇总"
    assert seen_tools == [True, True, False]  # 2 轮工具 + 1 轮强制收尾


def test_tool_error_retries_without_tools(monkeypatch):
    cfg = load_config({"OPENCODE_GO_API_KEY": "ok", "SEARCH_API_KEY": "fk"})
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append("tools" in body)
        if "tools" in body:
            return httpx.Response(400, text="Invalid parameter: tools is not supported by this model")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "好"}}]})

    completion = asyncio.run(
        llm.chat_once("hi", cfg.providers["opencode_go"], transport=httpx.MockTransport(handler))
    )
    assert completion.text == "好"
    assert seen == [True, False]


def test_chat_falls_back_until_success(monkeypatch):
    calls: list[str] = []

    async def fake_chat_once(text, provider, **kwargs):
        calls.append(provider.name)
        if provider.name == "local":
            raise httpx.ConnectError("llama not running")
        return llm.Completion(text="备用成功", reasoning="想了想")

    monkeypatch.setattr(llm, "chat_once", fake_chat_once)
    reply = asyncio.run(llm.chat("hi", chain=["local", "opencode_go"]))
    assert calls == ["local", "opencode_go"]
    assert reply.provider == "opencode_go"
    assert reply.text == "备用成功"
    assert reply.reasoning == "想了想"
