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


def _make_handler(monkeypatch, args, *, cfg=None, fail_on=None, quote_first=True):
    cfg = cfg or _cfg()
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    h = _Harness(fail_on=fail_on)
    records: list[tuple] = []
    monkeypatch.setattr(context, "record_message", lambda *a, **k: records.append(a))
    monkeypatch.setattr(context, "recent_messages", lambda *a, **k: [])  # 复读检查查空
    state = {"text": 0, "any": 0}
    handler = llm_chat._make_send_message_handler(h.send, _fake_event(), "十九", state, quote_first=quote_first)
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
    assert seen["names"] == [
        "send_message",
        "send_reaction",
        "read_history",
        "search_history",
        "remember",
        "recall",
        "set_reminder",
    ]  # fetch_url 需搜索 key：本测试未配置故不注入
    assert callable(seen["is_replied"]) and seen["is_replied"]() is False


# ── DSML 工具调用文本泄漏兜底（实测事故）─────────────────────────────────────
# 模型偶发把 send_message 调用序列化成 "<｜｜DSML｜｜ invoke name=...>" 文本（不走结构化
# tool_calls）。若不拦截会被当正文发进群（朋友群实际发出过一条乱码）。兜底策略：
# 命中即视为异常输出——绝不发进群、不记群上下文（非静默场景给一句人话）。

_DSML_SAMPLE = (
    '<｜｜DSML｜｜ calls>\n'
    '<｜｜DSML｜｜ invoke name="send_message">\n'
    '<｜｜DSML｜｜ parameter name="messages" string="false">"在吗"'
)


def test_looks_like_tool_leak_detection():
    from core import textnorm

    assert textnorm.looks_like_tool_leak(_DSML_SAMPLE) is True
    assert textnorm.looks_like_tool_leak("<|tool▁calls|>") is True
    assert textnorm.looks_like_tool_leak("今天天气不错哈哈") is False
    assert textnorm.looks_like_tool_leak("") is False
    assert textnorm.looks_like_tool_leak("DSML 是啥") is False  # 闲聊提到不带标记字符


def test_chat_flow_dsml_leak_blocked(tmp_path, monkeypatch):
    """兜底：DSML 正文不发群、不记上下文；非静默场景给一句人话。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        return llm.Reply(provider="x", text=_DSML_SAMPLE, reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    assert not any("DSML" in s for s in sent)       # 乱码没发出去
    assert any("卡了一下" in s for s in sent)       # 失败路径=人话（LLM_ERROR_REPLY）
    rows = context.recent_messages(709987676, limit=10)
    assert not any("DSML" in str(r.get("text") or "") for r in rows)  # 也没记进群上下文


def test_chat_flow_dsml_leak_quiet_silent(tmp_path, monkeypatch):
    """auto 接话（quiet_skip）场景：DSML 泄漏 → 完全静默。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        return llm.Reply(provider="x", text=_DSML_SAMPLE, reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(
        llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send, quiet_skip=True)
    )
    assert sent == []


def test_send_message_handler_skips_dsml(monkeypatch):
    """handler：单条为 DSML 乱码时跳过该条；其余条照常发。"""
    result, sent, state, _ = _make_handler(
        monkeypatch, {"messages": ["好的", _DSML_SAMPLE]}
    )
    assert len(sent) == 1 and "好的" in sent[0]
    assert state["text"] == 1
    assert "DSML" not in "".join(sent)


def test_chat_flow_send_failure_is_swallowed(tmp_path, monkeypatch):
    """发送失败（断连等）记日志即可：不再抛异常炸出 traceback（实测噪音）。"""
    _prep_flow(tmp_path, monkeypatch, _cfg())

    async def fake_chat(prompt, **kw):
        return llm.Reply(provider="x", text="正常回复", reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))
        raise RuntimeError("connection lost")

    # 不应抛异常（旧版会把 RuntimeError 抛到 matcher 层炸 traceback）
    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=_fake_event(), text="在吗", send=send))
    assert len(sent) == 1  # 尝试过发送（失败被吞）


# ── #3 引用克制：无歧义（触发消息=最新）不引用；指代可能不清才引用 ────────────


def test_should_quote_rules(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)

    ev = _fake_event()  # message_id=1001
    # 场景1：无任何记录（新群/探针）→ 保守引用
    assert llm_chat._should_quote(ev) is True
    # 场景2：触发消息就是最近最后一条 → 不引用（无歧义）
    context.record_message(ev.group_id, ev.user_id, "小红", "在吗", message_id=1001)
    assert llm_chat._should_quote(ev) is False
    # 场景3：触发消息之后又有新消息 → 引用（指代可能不清）
    context.record_message(ev.group_id, 944314364, "小明", "还有别的吗", message_id=1002)
    assert llm_chat._should_quote(ev) is True


def test_should_quote_disabled(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(QUOTE_REPLY_ENABLED="0"), raising=False)
    assert llm_chat._should_quote(_fake_event()) is False


def test_send_message_quote_first_toggle(monkeypatch):
    """quote_first=False（无歧义场景）时首条也不带引用段。"""
    _, sent, state, _ = _make_handler(monkeypatch, {"messages": ["在的", "咋了"]}, quote_first=False)
    assert len(sent) == 2
    assert "reply" not in sent[0]  # 无引用段
    assert sent[1] == "咋了"


# ── #4 复读防护：同处理内相同条目去重；60s 跨处理同文（≥8字）跳过 ─────────────


def test_send_message_in_batch_dedup(monkeypatch):
    """同一次工具调用内完全相同的条目去重。"""
    result, sent, state, _ = _make_handler(monkeypatch, {"messages": ["在吗", "在吗", "咋了"]})
    assert len(sent) == 2
    assert state["text"] == 2


def test_is_recent_repeat_rules(monkeypatch):
    import time as _time

    now = _time.time()
    rows = [{"user_id": 123456789, "text": "这是一条足够长的回复文本", "ts": now - 10}]
    monkeypatch.setattr(llm_chat.context, "recent_messages", lambda gid, limit=10: rows)
    assert llm_chat._is_recent_repeat(1, 123456789, "这是一条足够长的回复文本") is True
    assert llm_chat._is_recent_repeat(1, 123456789, "这是一条足够长的回复文本。") is False  # 不完全同
    assert llm_chat._is_recent_repeat(1, 123456789, "哈哈") is False  # 短回应豁免
    rows[0]["ts"] = now - 300
    assert llm_chat._is_recent_repeat(1, 123456789, "这是一条足够长的回复文本") is False  # 超出窗口
    rows[0]["ts"] = now - 10
    assert llm_chat._is_recent_repeat(1, 999, "这是一条足够长的回复文本") is False  # 别的发言人


def test_send_message_cross_repeat_skipped(monkeypatch):
    """近 60s 已发过的长文本 → 该条跳过（视为已在群里）；全被滤时空回执。"""
    import time as _time

    rows = [{"user_id": 123456789, "text": "这是一条足够长的回复文本", "ts": _time.time() - 10}]
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    monkeypatch.setattr(context, "record_message", lambda *a, **k: None)
    monkeypatch.setattr(context, "recent_messages", lambda *a, **k: rows)
    h = _Harness()
    state = {"text": 0, "any": 0}
    handler = llm_chat._make_send_message_handler(h.send, _fake_event(), "十九", state)
    result = asyncio.run(handler("send_message", {"messages": ["这是一条足够长的回复文本"]}))
    assert h.sent == []  # 没重复发
    assert "没有可发送" in result
    assert state["text"] == 0


def test_chat_flow_fallback_repeat_silent(tmp_path, monkeypatch):
    """终答正文与近 60s 已发内容完全相同（长文本）→ 静默（不重复刷）。"""
    import time as _time

    _prep_flow(tmp_path, monkeypatch, _cfg())
    ev = _fake_event()
    context.record_message(ev.group_id, ev.self_id, "十九", "这是一条足够长的回复文本", ts=_time.time() - 5)

    async def fake_chat(prompt, **kw):
        return llm.Reply(provider="x", text="这是一条足够长的回复文本", reasoning="", truncated=False, total_tokens=0)

    monkeypatch.setattr(llm_chat.llm, "chat", fake_chat)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=_FakeBot(sent), event=ev, text="在吗", send=send))
    assert sent == []  # 重复内容静默


# ── #6a/#6b 贴图回退链 + 贴表情 ──────────────────────────────────────────────


def test_sticker_handler_fallback_chain(tmp_path, monkeypatch):
    """#6a：第一张发送失败 → 自动换下一张（最多试 3 张）。"""
    from core import stickers as core_stickers

    context.reset(tmp_path / "ctx.db")
    core_stickers.reset()
    for name in ("a.jpg", "b.jpg"):
        f = tmp_path / name
        f.write_bytes(b"x" * 16)
        context.image_upsert(name, str(f), "测试表情包", "test")
    monkeypatch.setattr(core_config, "_config", _cfg(STICKER_ENABLED="1"), raising=False)

    sent: list[str] = []
    fails = {"n": 0}

    class _B:
        async def send(self, event, msg):
            fails["n"] += 1
            if fails["n"] == 1:
                raise RuntimeError("upload failed")
            sent.append(str(msg))

    state = {"text": 0, "any": 0}
    handler = llm_chat._make_sticker_handler(_B(), _fake_event(), state)
    result = asyncio.run(handler("send_sticker", {"query": "测试"}))
    assert "已发送" in result
    assert len(sent) == 1  # 第二张成功发出
    assert state["any"] == 1


def test_reaction_handler_menu_and_limits(monkeypatch):
    """#6b：菜单映射→set_msg_emoji_like；一轮只能贴一个；未知名给菜单提示。"""
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    calls: list[tuple] = []

    class _B:
        async def call_api(self, api, **kw):
            calls.append((api, kw))
            return {}

    state = {"text": 0, "any": 0}
    handler = llm_chat._make_reaction_handler(_B(), _fake_event(), state)
    r1 = asyncio.run(handler("send_reaction", {"emoji": "赞"}))
    assert calls and calls[0][0] == "set_msg_emoji_like"
    assert calls[0][1] == {"message_id": 1001, "emoji_id": "76"}
    assert state["any"] == 1 and "已贴上" in r1

    r2 = asyncio.run(handler("send_reaction", {"emoji": "笑哭"}))
    assert "别连贴" in r2 and len(calls) == 1  # 一轮一个

    handler2 = llm_chat._make_reaction_handler(_B(), _fake_event(), {"text": 0, "any": 0})
    r3 = asyncio.run(handler2("send_reaction", {"emoji": "不存在"}))
    assert "菜单里没有" in r3
