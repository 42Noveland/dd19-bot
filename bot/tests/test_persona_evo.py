import asyncio
import types

from core import config as core_config
from core import context, persona_evo, personas, style_pairs
from core.config import load_config


def _cfg():
    return load_config({})


def test_parse_proposals():
    raw = '```json\n{"text":"多接点游戏梗"}\n垃圾行\n{"text":"说话别太端着"}\n'
    assert persona_evo.parse_proposals(raw) == ["多接点游戏梗", "说话别太端着"]


def test_generate_review_approve_rollback(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(
        core_config, "_config", load_config({"LLM_SYSTEM_PROMPT": "# 人设：十九\n默认人设文本"}), raising=False
    )
    gid = 111
    # 种风格对（凑证据）
    context.record_message(gid, 29991, "小红", "今天吃啥好呢")
    context.record_message(gid, 123456789, "十九", "随便整点，别纠结")
    style_pairs.extract_group(gid, _cfg())

    calls: list[str] = []

    async def fake_chat_once(text, provider, **kw):
        calls.append(text)
        return types.SimpleNamespace(text='{"text":"多接一点吃的梗"}\n{"text":"说话再随意点"}')

    monkeypatch.setattr(persona_evo.llm, "chat_once", fake_chat_once)
    r = asyncio.run(persona_evo.generate_proposals(gid, _cfg()))
    assert r["generated"] == 2 and r["pending"] == 2
    # 去重：同文本不再生成
    r2 = asyncio.run(persona_evo.generate_proposals(gid, _cfg()))
    assert r2["generated"] == 0
    # 待审列表 + 批准 → 生效（resolve 拼上补充，且不改 md 原文）
    pend = persona_evo.list_pending(gid)
    assert len(pend) == 2
    res = persona_evo.approve(pend[0]["id"])
    assert res["ok"]
    _, text = personas.resolve(gid)
    assert "多接一点吃的梗" in text and "【本群学习补充" in text
    # 回滚
    patches = persona_evo.list_patches(gid)
    assert len(patches) == 1
    persona_evo.remove_patch(patches[0]["id"])
    _, text2 = personas.resolve(gid)
    assert "多接一点吃的梗" not in text2


def test_reject_and_guard(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", load_config({"LLM_SYSTEM_PROMPT": "人设X"}), raising=False)
    gid = 222
    # 没有风格对 → 不调 LLM
    calls: list[str] = []

    async def fake(text, provider, **kw):
        calls.append(text)
        return types.SimpleNamespace(text='{"text":"建议A"}')

    monkeypatch.setattr(persona_evo.llm, "chat_once", fake)
    r = asyncio.run(persona_evo.generate_proposals(gid, _cfg()))
    assert r["generated"] == 0 and calls == []
    # 注入待审 → 拒绝 → 无补充生效
    pid = persona_evo.add_pending(gid, "十九", "建议A")
    rej = persona_evo.reject(pid)
    assert rej["ok"]
    _, text = personas.resolve(gid)
    assert "建议A" not in text
    assert persona_evo.pending_count(gid) == 0
    # 重复处理：第二次失败
    assert persona_evo.approve(pid)["ok"] is False
