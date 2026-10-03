import asyncio
import types

from core import context, jargon
from core.config import load_config


def _cfg():
    return load_config({})


def _seed(term: str, n: int, gid: int = 111, users=(29991,)):
    for i in range(n):
        context.record_message(gid, users[i % len(users)], f"用户{i}", f"今天又{term}了哈哈")


def test_candidates_signals(tmp_path):
    context.reset(tmp_path / "ctx.db")
    # 群 111 特有词：蓝瘦 ×6（1 人）；普通词「今天」铺满两个群
    _seed("蓝瘦", 6)
    for _ in range(8):
        context.record_message(111, 29991, "小红", "今天天气真好啊")
        context.record_message(222, 29993, "老李", "今天干活好累啊")
    cands = jargon.candidates(111)
    terms = [c["term"] for c in cands]
    assert "蓝瘦" in terms
    by = {c["term"]: c for c in cands}
    # 跨群词 idf=0（出现在所有群）；群内特有词 idf>0 且总分更高
    assert by["今天"]["idf"] == 0.0
    assert by["蓝瘦"]["score"] > by["今天"]["score"]
    # 用户集中度：蓝瘦 1 人 = 1.0
    assert by["蓝瘦"]["users"] == 1


def test_parse_round_reply():
    raw = """```json
{"term":"蓝瘦","meaning":"难受的谐音梗","slang":true}
{"term":"今天","slang":false}
这行是垃圾
{"term":"","meaning":"x","slang":true}
"""
    items = jargon.parse_round_reply(raw)
    assert {"term": "蓝瘦", "meaning": "难受的谐音梗", "slang": True} in items
    assert any(i["term"] == "今天" and not i["slang"] for i in items)
    assert all(i["term"] for i in items)


def test_mine_round_pipeline(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    _seed("蓝瘦", 6)
    calls: list[str] = []

    async def fake_chat_once(text, provider, **kw):
        calls.append(text)
        return types.SimpleNamespace(text='{"term":"蓝瘦","meaning":"难受的谐音梗","slang":true}')

    monkeypatch.setattr(jargon.llm, "chat_once", fake_chat_once)
    r = asyncio.run(jargon.mine_round(111, _cfg()))
    assert r["saved"] >= 1 and r["known"] == 1
    block = jargon.for_prompt(111, "今天好蓝瘦啊")
    assert "蓝瘦" in block and "只用于理解" in block
    assert jargon.for_prompt(111, "完全无关的一句话") == ""
    # 第二轮：蓝瘦已入表 → 不再进入候选（其它候选可能仍在，但候选列表里不再有「蓝瘦」；
    # 注意例子文本里可能仍含"蓝瘦"字样，要检查候选标记「蓝瘦」）
    r2 = asyncio.run(jargon.mine_round(111, _cfg()))
    assert r2["saved"] == 0
    assert "「蓝瘦」" not in calls[-1]


def test_mine_round_no_candidates(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    context.record_message(111, 29991, "小红", "随便说一句话")
    calls: list[str] = []

    async def fake(text, provider, **kw):
        calls.append(text)
        return types.SimpleNamespace(text="")

    monkeypatch.setattr(jargon.llm, "chat_once", fake)
    r = asyncio.run(jargon.mine_round(111, _cfg()))
    assert r["candidates"] == 0 and calls == []
