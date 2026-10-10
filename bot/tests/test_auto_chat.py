"""auto_chat 预筛行为：接话冷却（点名穿透）+ per-sender 跟聊窗口。

- 每群冷却 AUTO_REPLY_COOLDOWN>0 时：接话后的静默期内，非点名消息（含跟聊窗口）
  不再接话（防"人机对线"刷屏）；被点名可穿透。0=不冷却。
- 跟聊窗口（per-sender，参考 qq-agent short-followup）：bot 回复过的人，
  _shared.FOLLOWUP_WINDOW 秒内其消息跳过概率门必判；别人不受影响。
  注：默认冷却 240s ≥ 窗口 120s，静默期内窗口不生效（冷却期外仍有效）。
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
from plugins import _shared, auto_chat  # noqa: E402


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


def _run_consider(monkeypatch, cfg, recent, *, last_auto=None, followup=None,
                  text="大家明天出去玩吗"):
    """跑一遍 _consider，返回被调度的接话判定列表。

    followup=(gid, uid[, window])：先清窗口，再为该 (群,人) 开窗（模拟 bot 刚回复过）。
    text：触发消息文本（点名场景用）。
    """
    monkeypatch.setattr(core_config, "_config", cfg, raising=False)
    monkeypatch.setattr(auto_chat.context, "recent_messages", lambda gid, limit=8: recent)
    _shared.clear_followups()
    if followup:
        _shared.note_replied(*followup)
    called: list[int] = []

    async def fake_judge(bot, event):
        called.append(event.message_id)

    monkeypatch.setattr(auto_chat, "_judge_and_maybe_reply", fake_judge)
    auto_chat._last_auto.clear()
    auto_chat._inflight.clear()
    if last_auto:
        auto_chat._last_auto.update(last_auto)

    async def go():
        await auto_chat._consider(_fake_bot(), _fake_event(text))
        await asyncio.sleep(0.05)  # 让 create_task 调度的判定跑完

    asyncio.run(go())
    return called


def test_plain_message_still_judged_without_window(monkeypatch):
    """无跟聊窗口：chance=1.0 普通消息照常进入判定（预筛不拦）。"""
    called = _run_consider(monkeypatch, _cfg(), [])
    assert called == [1001]


def test_followup_same_sender_forces_judge(monkeypatch):
    """per-sender 窗口：bot 回复过的人，窗口内其消息跳过概率门必判（chance=0 也判）。"""
    called = _run_consider(
        monkeypatch, _cfg(AUTO_REPLY_CHANCE="0.0"), [], followup=(709987676, 944314363)
    )
    assert called == [1001]


def test_followup_other_sender_not_forced(monkeypatch):
    """窗口只认被回复的人：窗口里记的是别人时，chance=0 不判。"""
    called = _run_consider(
        monkeypatch, _cfg(AUTO_REPLY_CHANCE="0.0"), [], followup=(709987676, 555555555)
    )
    assert called == []


def test_followup_expired_not_forced(monkeypatch):
    """窗口过期（负时长）后不再必判：chance=0 不判。"""
    called = _run_consider(
        monkeypatch, _cfg(AUTO_REPLY_CHANCE="0.0"), [], followup=(709987676, 944314363, -1)
    )
    assert called == []


def test_cooldown_blocks_plain_message(monkeypatch):
    """冷却>0：接话后的静默期内，普通消息不接（降频语义）。"""
    called = _run_consider(
        monkeypatch,
        _cfg(AUTO_REPLY_COOLDOWN="9999"),
        [],
        last_auto={709987676: time.time()},  # 刚接过话
    )
    assert called == []


def test_cooldown_blocks_followup_too(monkeypatch):
    """冷却期内跟聊窗口不豁免：被回复过的人说话也拦（防"人机对线"刷屏）。"""
    called = _run_consider(
        monkeypatch,
        _cfg(AUTO_REPLY_COOLDOWN="9999"),
        [],
        last_auto={709987676: time.time()},
        followup=(709987676, 944314363),
    )
    assert called == []


def test_cooldown_named_message_penetrates(monkeypatch):
    """冷却期内被点名（文本点名）→ 穿透冷却，照常判定。"""
    monkeypatch.setattr(auto_chat.personas, "name_for", lambda gid: "十九")
    called = _run_consider(
        monkeypatch,
        _cfg(AUTO_REPLY_COOLDOWN="9999"),
        [],
        last_auto={709987676: time.time()},
        text="十九在吗，帮我看个东西",
    )
    assert called == [1001]


def test_cooldown_expired_judges_normally(monkeypatch):
    """冷却期已过 → 普通消息照常进入判定。"""
    called = _run_consider(
        monkeypatch,
        _cfg(AUTO_REPLY_COOLDOWN="9999"),
        [],
        last_auto={709987676: time.time() - 10000},
    )
    assert called == [1001]


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
