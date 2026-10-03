import asyncio
import time
import types

from core import context, mood
from core.config import load_config


def test_decay():
    assert abs(mood.decay(1.0, 0) - 1.0) < 1e-9
    assert abs(mood.decay(1.0, mood.HALF_LIFE) - 0.5) < 1e-6
    assert mood.decay(1.0, mood.HALF_LIFE * 10) < 0.01


def test_parse_mood_update():
    assert mood.parse_mood_update('{"mood":"得意","intensity":0.8,"reason":"被夸了"}')["mood"] == "得意"
    assert mood.parse_mood_update('```json\n{"mood":"开心","intensity":2,"reason":"x"}\n```')["intensity"] == 1.0
    assert mood.parse_mood_update('{"mood":"不知道","intensity":0.5}') is None
    assert mood.parse_mood_update("垃圾输出") is None


def test_set_get_decay_prompt(tmp_path):
    context.reset(tmp_path / "ctx.db")
    assert mood.get_mood(111) is None
    mood.set_mood(111, "得意", 0.9, "刚被夸了")
    m = mood.get_mood(111)
    assert m and m["mood"] == "得意" and m["intensity"] > 0.8
    block = mood.for_prompt(111)
    assert "得意" in block and "被夸" in block
    # 时间流逝后衰减到阈值以下 → 回归平静
    later = time.time() + mood.HALF_LIFE * 3
    assert mood.get_mood(111, now=later) is None
    assert mood.for_prompt(111, now=later) == ""
    # 平静不注入
    mood.set_mood(111, "平静", 0.9, "没啥事")
    assert mood.get_mood(111) is None


def test_update_group_pipeline(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    for i in range(7):
        context.record_message(111, 222, "小明", f"消息{i}")
    calls: list[str] = []

    async def fake_chat_once(text, provider, **kw):
        calls.append(text)
        return types.SimpleNamespace(text='{"mood":"开心","intensity":0.7,"reason":"聊得挺欢"}')

    monkeypatch.setattr(mood.llm, "chat_once", fake_chat_once)
    cfg = load_config({})
    ok = asyncio.run(mood.update_group(111, cfg))
    assert ok is True
    assert mood.get_mood(111)["mood"] == "开心"
    # 刚更新过，没有新消息 → 跳过
    assert asyncio.run(mood.update_group(111, cfg)) is False
    assert len(calls) == 1
