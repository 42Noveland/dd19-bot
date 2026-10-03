import asyncio
import types

from core import context, memory
from core.config import load_config


def test_parse_extraction():
    raw = '''```json
{"kind":"user","name":"小明","fact":"喜欢蓝色"}
{"kind":"group","fact":"大家聊过养猫"}
这行是垃圾
{"kind":"user","name":"","fact":"没有名字"}
[{"kind":"user","name":"小丽","fact":"爱笑"}]
'''
    items = memory.parse_extraction(raw)
    kinds = [(i["kind"], i.get("name", "")) for i in items]
    assert ("user", "小明") in kinds
    assert any(i["kind"] == "group" and i["fact"] == "大家聊过养猫" for i in items)
    assert any(i["kind"] == "user" and i["name"] == "小丽" for i in items)
    assert all(i["fact"] != "没有名字" for i in items)


def test_add_memory_dedup_and_cap(tmp_path):
    context.reset(tmp_path / "ctx.db")
    assert memory.add_memory(111, 222, "小明", "user", "喜欢蓝色") is True
    assert memory.add_memory(111, 222, "小明", "user", "喜欢蓝色") is False  # 去重
    for i in range(memory.MAX_PER_USER + 5):
        memory.add_memory(111, 222, "小明", "user", f"事实{i}")
    assert memory.stats_for(111)["user"] == memory.MAX_PER_USER  # 上限淘汰


def test_for_prompt_empty(tmp_path):
    context.reset(tmp_path / "ctx.db")
    assert memory.for_prompt(111, 222) == ""


def test_extract_group_pipeline(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    context.record_message(111, 222, "小明", "我最喜欢的颜色是蓝色")
    context.record_message(111, 333, "小丽", "我养了一只猫")
    calls: list[str] = []

    async def fake_chat_once(text, provider, **kw):
        calls.append(text)
        return types.SimpleNamespace(
            text='{"kind":"user","name":"小明","fact":"最喜欢蓝色"}\n{"kind":"group","fact":"小丽养了只猫"}'
        )

    monkeypatch.setattr(memory.llm, "chat_once", fake_chat_once)
    cfg = load_config({})
    processed, added = asyncio.run(memory.extract_group(111, cfg))
    assert processed == 2 and added == 2
    st = memory.stats_for(111)
    assert st["user"] == 1 and st["group"] == 1 and st["pending"] == 0
    # 增量：再跑一次没有新消息
    processed2, added2 = asyncio.run(memory.extract_group(111, cfg))
    assert processed2 == 0 and added2 == 0
    block = memory.for_prompt(111, 222)
    assert "最喜欢蓝色" in block and "小丽养了只猫" in block
    assert len(calls) == 1
