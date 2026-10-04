"""失败路径文案单测：群可见的失败必须是人话（不暴露异常细节）。"""
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


def _cfg():
    return load_config({"LLM_ENABLED": "1", "STICKER_ENABLED": "0", "LLM_SYSTEM_PROMPT": "人设X"})


def _fake_event(gid: int, uid: int) -> types.SimpleNamespace:
    ev = types.SimpleNamespace()
    ev.group_id = gid
    ev.user_id = uid
    ev.self_id = 123456789
    ev.message_id = 123456
    ev.sender = types.SimpleNamespace(card="小红", nickname="小红")
    ev.get_message = lambda: []
    return ev


def test_chat_flow_failure_is_humanized(tmp_path, monkeypatch):
    """LLM 全挂时：只发人话（LLM_ERROR_REPLY），异常名与原文都不出现在群里。"""
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    monkeypatch.setattr(core_budget, "exhausted", lambda *a, **k: False)
    llm.reset_rate_limit()

    async def boom(*a, **k):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(llm_chat.llm, "chat", boom)
    sent: list[str] = []

    async def send(msg):
        sent.append(str(msg))

    asyncio.run(llm_chat.chat_flow(bot=None, event=_fake_event(709900001, 29991), text="你好", send=send))
    assert sent, "应该有一条回复"
    out = sent[-1]
    assert "卡了一下" in out
    assert "AI 调用失败" not in out
    assert "RuntimeError" not in out and "upstream exploded" not in out


def test_search_failure_is_humanized(tmp_path, monkeypatch):
    """搜索失败时：只发人话，异常名不出现在群里。"""
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    monkeypatch.setattr(core_budget, "exhausted", lambda *a, **k: False)
    llm.reset_rate_limit()

    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm_chat.search, "web_search", boom)
    finished: list[str] = []

    class FakeMatcher:
        async def finish(self, msg):
            finished.append(str(msg))

    asyncio.run(llm_chat._do_search(FakeMatcher(), _fake_event(709900002, 29992), "天气"))
    assert finished, "应该有一条回复"
    out = finished[-1]
    assert "搜索失败了" in out
    assert "RuntimeError" not in out and "network down" not in out


def test_format_reply_cleans_but_keeps_our_notes(monkeypatch):
    """清洗只针对模型原文；我们追加的截断提示（同为中文括号）必须保留。"""
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    r = llm.Reply(provider="x", text="好的（旁白）没问题", reasoning="", truncated=True, total_tokens=0)
    out = llm_chat._format_reply(r)
    assert "旁白" not in out
    assert "截断" in out
