"""send_message 协议单测：工具 handler 行为 + chat_flow 双轨 + 已发送静默。

覆盖：
  - handler：字符串/数组/清洗/上限/参数错误/部分失败/存档/state 计数
  - 双轨：调用过工具 → 正文丢弃；未调用 → 正文照发（现状路径）
  - 异常：已发送后失败静默（不刷"卡了一下"）；未发送时保持人话
  - 开关：SEND_PROTOCOL_ENABLED=0 完整回滚（不注入工具、正文照发）
"""
import asyncio
import types

import nonebot
from nonebot.config import Config

# 插件模块导入需要一个最小的 nonebot 环境（不启动任何服务/驱动）
nonebot._driver = types.SimpleNamespace(config=Config())

from core import budget as core_budget  # noqa: E402
from core import config as core_config  # noqa: E402
from core import context, llm  # noqa: E402
from core.config import load_config  # noqa: E402
from plugins import llm_chat  # noqa: E402


def _cfg(**over):
    env = {
        "LLM_ENABLED": "1",
        "STICKER_ENABLED": "0",
        "LLM_SYSTEM_PROMPT": "人设X",
        "SEND_PROTOCOL_ENABLED": "1",
    }
    env.update(over)
    return load_config(env)


def _fake_event(gid: int = 709900003, uid: int = 29993):
    ev = types.SimpleNamespace()
    ev.group_id = gid
    ev.user_id = uid
    ev.self_id = 123456789
    ev.message_id = 1001
    ev.sender = types.SimpleNamespace(card="小红", nickname="小红")
    ev.get_message = lambda: []
    return ev


class _Harness:
    """收集发送内容；可指定第 N 次发送抛错（模拟部分失败）。"""

    def __init__(self, fail_on: int | None = None):
        self.sent: list[str] = []
        self.fail_on = fail_on
        self.calls = 0

    async def send(self, msg):
        self.calls += 1
        if self.fail_on and self.calls >= self.fail_on:
            raise RuntimeError("send timeout")
        self.sent.append(str(msg))


class _FakeBot:
    """chat_flow 工具发送（bot.send）的收集器：工具发送走直发、不进 send 回调。"""

    def __init__(self, sink: list[str]):
        self.sink = sink

    async def send(self, event, msg):
        self.sink.append(str(msg))


def _make_handler(monkeypatch, args, *, cfg=None, fail_on=None):
    cfg = cfg or _cfg()
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    h = _Harness(fail_on=fail_on)
    records: list[tuple] = []
    monkeypatch.setattr(context, "record_message", lambda *a, **k: records.append(a))
    state = {"text": 0, "any": 0}
    handler = llm_chat._make_send_message_handler(h.send, _fake_event(), "十九", state)
    result = asyncio.run(handler("send_message", args))
    return result, h.sent, state, records


# ── handler 行为 ──────────────────────────────────────────────────────────


def test_send_message_single_string(monkeypatch):
    result, sent, state, records = _make_handler(monkeypatch, {"messages": "在的"})
    assert len(sent) == 1 and "在的" in sent[0]
    assert state["text"] == 1 and state["any"] == 1
    assert len(records) == 1 and records[0][2] == "十九"  # 记录名=人设名
    assert "已发送" in result and "汇报" in result


def test_send_message_list_multi_with_quote_only_first(monkeypatch):
    result, sent, state, _ = _make_handler(monkeypatch, {"messages": ["在的", "咋了"]})
    assert len(sent) == 2
    assert "在的" in sent[0] and "reply" in sent[0]  # 首条带引用段
    assert sent[1] == "咋了"                          # 后续条纯文本、不带引用
    assert state["text"] == 2


def test_send_message_cleans_and_filters(monkeypatch):
    result, sent, state, _ = _make_handler(monkeypatch, {"messages": ["好的（旁白）", "   ", ""]})
    assert len(sent) == 1
    assert "好的" in sent[0] and "旁白" not in sent[0]  # clean_reply 生效
    assert state["text"] == 1


def test_send_message_caps_at_three(monkeypatch):
    result, sent, state, _ = _make_handler(monkeypatch, {"messages": ["一", "二", "三", "四"]})
    assert len(sent) == 3
    assert state["text"] == 3
    assert "最多" in result and "再调一次" in result


def test_send_message_invalid_args(monkeypatch):
    result, sent, state, _ = _make_handler(monkeypatch, {"messages": 123})
    assert not sent and state["text"] == 0
    assert "参数错误" in result


def test_send_message_partial_failure(monkeypatch):
    result, sent, state, _ = _make_handler(monkeypatch, {"messages": ["第一条", "第二条"]}, fail_on=2)
    assert len(sent) == 1 and "第一条" in sent[0]
    assert state["text"] == 1
    assert "没发出去" in result


def test_send_message_handler_reraises_finished_exception(monkeypatch):
    """防回归（实测事故）：send 抛 FinishedException（nonebot 控制流）时必须重抛，
    绝不能记成"发送失败"——被吞后模型会以为没发出去而反复重写重发（刷屏）。"""
    import pytest as _pytest

    from nonebot.exception import FinishedException

    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)

    async def send(msg):
        raise FinishedException

    state = {"text": 0, "any": 0}
    handler = llm_chat._make_send_message_handler(send, _fake_event(), "十九", state)
    with _pytest.raises(FinishedException):
        asyncio.run(handler("send_message", {"messages": ["一", "二"]}))
    assert state["text"] == 0  # 控制流异常不计入"已发送"


# ── chat_flow 双轨 ─────────────────────────────────────────────────────────


def _prep_flow(tmp_path, monkeypatch, cfg):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    monkeypatch.setattr(core_budget, "exhausted", lambda *a, **k: False)
    llm.reset_rate_limit()


def test_chat_flow_tool_reply_drops_plain_text(tmp_path, monkeypatch):
    """模型调用过 send_message → 终答正文不再发送（思考不外泄）。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        th = kw.get("tool_handler")
        assert th is not None, "协议开启时应注入 send_message 工具"
        await th("send_message", {"messages": ["收到", "马上看"]})
        return llm.Reply(provider="x", text="（内心独白：这样就回完了）", reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="帮我看看", send=send))
    joined = "".join(sent)
    assert "收到" in joined and "马上看" in joined
    assert "内心独白" not in joined  # 正文被丢弃


def test_chat_flow_tool_send_bypasses_finish_style_send(tmp_path, monkeypatch):
    """防回归（实测事故）：传入的 send 是 finish 风格（发送即抛 FinishedException）时，
    工具的多条发送仍走直发、全部正常发出：不被中断、不计失败、终答正文被丢弃。"""
    from nonebot.exception import FinishedException

    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        th = kw.get("tool_handler")
        await th("send_message", {"messages": ["一条", "两条", "三条"]})
        return llm.Reply(provider="x", text="这是正文，不应发送", reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []
    finish_calls = {"n": 0}

    async def send(msg):  # 模拟 matcher.finish：发送后抛控制流异常
        finish_calls["n"] += 1
        sent.append(str(msg))
        raise FinishedException

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    assert finish_calls["n"] == 0  # 收尾 send 未被触发：工具发送绕开了它
    for piece in ("一条", "两条", "三条"):
        assert any(piece in s for s in sent), f"{piece} 未被发出"
    assert all("正文" not in s for s in sent)


def test_chat_flow_text_fallback_when_no_tool_call(tmp_path, monkeypatch):
    """模型没用工具 → 兜底轨道：正文照发（行为=现状）。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        return llm.Reply(provider="x", text="好呀，我在", reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    assert "好呀，我在" in "".join(sent)


def test_chat_flow_sent_then_error_is_silent(tmp_path, monkeypatch):
    """已发过消息后 LLM 抛错 → 静默（不刷"卡了一下"）。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        th = kw.get("tool_handler")
        await th("send_message", {"messages": "在的"})
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    joined = "".join(sent)
    assert "在的" in joined
    assert "卡了一下" not in joined and "RuntimeError" not in joined


def test_chat_flow_error_without_send_still_human(tmp_path, monkeypatch):
    """没发过消息时失败 → 保持人话（现状路径不回归）。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    assert "卡了一下" in "".join(sent)


def test_chat_flow_protocol_disabled_full_rollback(tmp_path, monkeypatch):
    """SEND_PROTOCOL_ENABLED=0：不注入 send_message，正文照发（完整回滚）。"""
    _prep_flow(tmp_path, monkeypatch, _cfg(SEND_PROTOCOL_ENABLED="0"))

    async def fake_chat(prompt, **kw):
        tools = kw.get("extra_tools") or []
        names = [t.get("function", {}).get("name") for t in tools]
        assert "send_message" not in names, "关闭协议时不应注入 send_message"
        return llm.Reply(provider="x", text="回滚模式的回复", reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    assert "回滚模式的回复" in "".join(sent)


def test_chat_flow_protocol_injects_spec(tmp_path, monkeypatch):
    """协议开启：send_message spec 在 extra_tools 里，is_replied 回调已传入。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())
    seen = {}

    async def fake_chat(prompt, **kw):
        tools = kw.get("extra_tools") or []
        seen["names"] = [t.get("function", {}).get("name") for t in tools]
        seen["is_replied"] = kw.get("is_replied")
        return llm.Reply(provider="x", text="好", reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    assert seen["names"] == ["send_message"]
    assert callable(seen["is_replied"]) and seen["is_replied"]() is False
