"""D 组工具单测（#11 翻记录 / #13 提醒 / #14 记忆）+ 提醒调度。"""
import asyncio
import time
import types

import nonebot
from nonebot.config import Config


def _noop_deco(*a, **k):
    def deco(f):
        return f

    return deco


nonebot._driver = types.SimpleNamespace(config=Config(), on_startup=_noop_deco, on_ready=_noop_deco)

from core import config as core_config  # noqa: E402
from core import context, memory  # noqa: E402
from core.config import load_config  # noqa: E402
from plugins import llm_chat, reminder_loop  # noqa: E402


def _cfg(**over):
    env = {
        "LLM_ENABLED": "1",
        "STICKER_ENABLED": "0",
        "LLM_SYSTEM_PROMPT": "人设X",
        "SEND_PROTOCOL_ENABLED": "1",
    }
    env.update(over)
    return load_config(env)


def _fake_event(gid=709900003, uid=29993, mid=1001):
    ev = types.SimpleNamespace()
    ev.group_id, ev.user_id, ev.self_id, ev.message_id = gid, uid, 123456789, mid
    ev.sender = types.SimpleNamespace(card="小红", nickname="小红")
    ev.get_message = lambda: []
    return ev


def test_history_handlers(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    ev = _fake_event()
    context.record_message(ev.group_id, 10001, "甲", "今天聊聊爬山", message_id=900)
    context.record_message(ev.group_id, 10002, "乙", "想去香山", message_id=901)

    h = llm_chat._make_history_handler(ev, search=False)
    out = asyncio.run(h("read_history", {"count": 10}))
    assert "爬山" in out and "香山" in out and "仅供回顾" in out

    h2 = llm_chat._make_history_handler(ev, search=True)
    assert "香山" in asyncio.run(h2("search_history", {"keyword": "香山"}))
    assert "没搜到" in asyncio.run(h2("search_history", {"keyword": "不存在的词"}))


def test_remember_and_recall(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    ev = _fake_event()
    h = llm_chat._make_remember_handler(ev)
    out = asyncio.run(h("remember", {"text": "小红喜欢喝美式"}))
    assert "记住了" in out
    rows = memory.search(ev.group_id, "美式")
    assert rows and "美式" in rows[0]["text"]

    h2 = llm_chat._make_recall_handler(ev)
    assert "美式" in asyncio.run(h2("recall", {"keyword": "美式"}))
    assert "没记过" in asyncio.run(h2("recall", {"keyword": "不存在的关键词"}))


def test_reminder_handler_and_delivery(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    ev = _fake_event()
    h = llm_chat._make_reminder_handler(ev)
    assert "时间要在" in asyncio.run(h("set_reminder", {"delay_minutes": 0, "text": "x"}))
    out = asyncio.run(h("set_reminder", {"delay_minutes": 5, "text": "吃药"}))
    assert "5 分钟后" in out
    assert len(context.reminder_list(ev.group_id, ev.user_id)) == 1

    class _Bot:
        def __init__(self):
            self.sent = []

        async def send_group_msg(self, group_id, message):
            self.sent.append((group_id, str(message)))

    conn = context._db()
    conn.execute("UPDATE reminders SET remind_ts=?", (time.time() - 1,))
    conn.commit()
    bot = _Bot()
    n = asyncio.run(reminder_loop.deliver_due(bot))
    assert n == 1
    assert bot.sent and "[CQ:at" in bot.sent[0][1] and "吃药" in bot.sent[0][1]
    assert context.reminder_due() == []  # 已标完成

    rid = context.reminder_add(ev.group_id, ev.user_id, "测试", time.time() + 600)
    assert context.reminder_cancel(rid, ev.group_id, ev.user_id) is True
    assert context.reminder_cancel(rid, ev.group_id, 999) is False  # 只能取消自己的
