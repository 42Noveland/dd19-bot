"""出站 @ 解析与入站别名：修复"假 @"（文本 @ 不是 at 段）。

- build_at_segments：文本 "@昵称" → [text, at, text...]，匹配不到返回 None（保留文字）
- _bot_aliases：文字 @ 兼容的别名包含 人设名/配置名/登录昵称/QQ
"""
import asyncio
import types

import nonebot
from nonebot.config import Config

nonebot._driver = types.SimpleNamespace(config=Config())

from core import config as core_config  # noqa: E402
from core.config import load_config  # noqa: E402
from plugins import llm_chat  # noqa: E402


def test_at_head():
    members = {"绯英": 2854207043}
    segs = llm_chat.build_at_segments("@绯英 你好", members)
    assert segs
    assert segs[0].type == "at" and segs[0].data["qq"] == "2854207043"
    tail = "".join(str(s.data.get("text", "")) for s in segs if s.type == "text")
    assert "你好" in tail


def test_at_mid_with_trailing_punct():
    members = {"绯英": 2854207043}
    segs = llm_chat.build_at_segments("喂 @绯英，在吗", members)
    assert segs and sum(1 for s in segs if s.type == "at") == 1
    tail = "".join(str(s.data.get("text", "")) for s in segs if s.type == "text")
    assert "喂 " in tail and "在吗" in tail and "  " not in tail  # 不产生双空格


def test_no_match_or_email():
    members = {"绯英": 2854207043}
    assert llm_chat.build_at_segments("@不存在 你好", members) is None
    assert llm_chat.build_at_segments("a@b.com 看看", members) is None  # 邮箱不误伤
    assert llm_chat.build_at_segments("没有艾特", members) is None


def test_multiple_ats():
    members = {"绯英": 1, "鱼鱼": 2}
    segs = llm_chat.build_at_segments("@绯英 @鱼鱼 来", members)
    assert segs and sum(1 for s in segs if s.type == "at") == 2


def test_bot_aliases_include_persona_and_nickname(monkeypatch):
    """文字 @ 的别名：QQ + 登录昵称 + 人设名 + 配置名（修复群友 @小鲸鱼 不响应）。"""
    class FakeBot:
        self_id = "180517257"

        async def get_login_info(self):
            return {"nickname": "鱼鱼"}

    monkeypatch.setattr(core_config, "_config", load_config({}), raising=False)
    monkeypatch.setattr(llm_chat.personas, "name_for", lambda gid: "小鲸鱼")
    llm_chat._MENTION_ALIASES.clear()
    aliases = asyncio.run(llm_chat._bot_aliases(FakeBot(), 1124845982))
    assert "180517257" in aliases
    assert "鱼鱼" in aliases
    assert "小鲸鱼" in aliases
