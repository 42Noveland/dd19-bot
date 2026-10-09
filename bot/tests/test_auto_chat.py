"""auto_chat 预筛行为：无冷却设计。

- 每群冷却 AUTO_REPLY_COOLDOWN=0 时不拦任何消息；
- 旧的"90 秒内刚说过话不抢话"硬保护已移除——bot 刚说完话，新消息仍进入接话判定
  （防止"刚回过一句就不再理人"的体感；是否真的接由 LLM 判定宁缺毋滥）。
"""
import asyncio
import time
import types

import nonebot
from nonebot.adapters.onebot.v11 import Message
from nonebot.config import Config

# 插件模块导入需要一个最小的 nonebot 环境（不启动任何服务/驱动）
nonebot._driver = types.SimpleNamespace(config=Config())

from core import config as core_config  # noqa: E402
from core.config import load_config  # noqa: E402
from plugins import auto_chat  # noqa: E402


def _cfg(**over):
    env = {
        "LLM_ENABLED": "1",
        "AUTO_REPLY_ENABLED": "1",
        "AUTO_REPLY_CHANCE": "1.0",
        "AUTO_REPLY_COOLDOWN": "0",
    }
    env.update(over)
    return load_config(env)


def _fake_bot():
    return types.SimpleNamespace(self_id="180517257")


def _fake_event(text: str = "大家明天出去玩吗"):
    ev = types.SimpleNamespace()
    ev.group_id = 709987676
    ev.self_id = 180517257
    ev.user_id = 944314363
    ev.message_id = 1001
    ev.is_tome = lambda: False
    ev.get_message = lambda: Message(text)
    return ev


def _run_consider(monkeypatch, cfg, recent, *, last_auto=None):
    """跑一遍 _consider，返回被调度的接话判定列表。"""
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    monkeypatch.setattr(auto_chat.context, "recent_messages", lambda gid, limit=8: recent)
    called: list[int] = []

    async def fake_judge(bot, event):
        called.append(event.message_id)

    monkeypatch.setattr(auto_chat, "_judge_and_maybe_reply", fake_judge)
    auto_chat._last_auto.clear()
    auto_chat._inflight.clear()
    if last_auto:
        auto_chat._last_auto.update(last_auto)

    async def go():
        await auto_chat._consider(_fake_bot(), _fake_event())
        await asyncio.sleep(0.05)  # 让 create_task 调度的判定跑完

    asyncio.run(go())
    return called


def test_recent_bot_message_does_not_block(monkeypatch):
    """bot 刚说过话 + 无冷却：新消息仍进入接话判定（旧 90s"不抢话"保护已删）。"""
    now = time.time()
    recent = [
        {"user_id": 180517257, "ts": now - 3, "message_id": 999},  # bot 3 秒前刚说过话
        {"user_id": 944314363, "ts": now - 5, "message_id": 1000},
    ]
    called = _run_consider(monkeypatch, _cfg(), recent)
    assert called == [1001]


def test_bot_recent_message_forces_judge(monkeypatch):
    """对话延续窗口：bot 120s 内在本群说过话 → 跳过概率门、必判（即使机会=0）。"""
    now = time.time()
    recent = [{"user_id": 180517257, "ts": now - 30, "message_id": 999}]
    called = _run_consider(monkeypatch, _cfg(AUTO_REPLY_CHANCE="0.0"), recent)
    assert called == [1001]


def test_stale_bot_message_does_not_force(monkeypatch):
    """窗口外（>120s）的 bot 消息不触发必判：机会=0 时不判。"""
    now = time.time()
    recent = [{"user_id": 180517257, "ts": now - 600, "message_id": 999}]
    called = _run_consider(monkeypatch, _cfg(AUTO_REPLY_CHANCE="0.0"), recent)
    assert called == []


def test_cooldown_still_respected_when_configured(monkeypatch):
    """冷却>0 时仍拦（配置项语义保留：0=不冷却）。"""
    called = _run_consider(
        monkeypatch,
        _cfg(AUTO_REPLY_COOLDOWN="9999"),
        [],
        last_auto={709987676: time.time()},  # 刚接过话
    )
    assert called == []


def test_chance_gate_still_works(monkeypatch):
    """概率门兜底仍在：chance=0 时普通消息不进入判定。"""
    called = _run_consider(monkeypatch, _cfg(AUTO_REPLY_CHANCE="0.0"), [])
    assert called == []


def test_debounce_merges_rapid_messages(monkeypatch):
    """#7：同人短窗连发 → 只判定一次（合并文本进判定材料）。"""
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    monkeypatch.setattr(auto_chat.debounce, "WINDOW", 0.05)
    auto_chat.debounce.reset()
    monkeypatch.setattr(auto_chat.context, "recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(auto_chat.personas, "resolve", lambda gid: ("十九", "人设"))

    calls: list[str] = []

    async def fake_chat_once(prompt, provider, **kw):
        calls.append(str(prompt))
        return types.SimpleNamespace(text="不接")

    monkeypatch.setattr(auto_chat.llm, "chat_once", fake_chat_once)

    ev1 = _fake_event("在吗")
    ev2 = _fake_event("忙不忙")

    async def go():
        t1 = asyncio.create_task(auto_chat._judge_and_maybe_reply(_fake_bot(), ev1))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(auto_chat._judge_and_maybe_reply(_fake_bot(), ev2))
        await asyncio.gather(t1, t2)

    asyncio.run(go())
    assert len(calls) == 1  # 只判定一次（第一条被取代）
    assert "在吗" in calls[0] and "忙不忙" in calls[0]  # 合并文本都在材料里
