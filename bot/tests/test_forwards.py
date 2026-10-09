"""#12 合并转发展开单测：节点渲染 / 无转发 / 接口失败静默。"""
import asyncio
import types

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.config import Config

nonebot._driver = types.SimpleNamespace(config=Config())

from plugins._shared import expand_forwards  # noqa: E402


class _B:
    def __init__(self, payload=None, fail=False):
        self.payload = payload
        self.fail = fail
        self.calls = []

    async def call_api(self, api, **kw):
        self.calls.append((api, kw))
        if self.fail:
            raise RuntimeError("no")
        return self.payload


def _msg_with_forward(fid="f1"):
    return Message([MessageSegment(type="forward", data={"id": fid})])


def test_expand_renders_nodes():
    payload = {
        "messages": [
            {
                "type": "node",
                "data": {
                    "nickname": "甲",
                    "content": [{"type": "text", "data": {"text": "你好"}}, {"type": "image", "data": {}}],
                },
            },
            {"type": "node", "data": {"user_id": "123", "content": [{"type": "text", "data": {"text": "在吗"}}]}},
        ]
    }
    out = asyncio.run(expand_forwards(_B(payload), _msg_with_forward()))
    assert "[转发记录]" in out
    assert "甲: 你好[图片]" in out
    assert "123: 在吗" in out


def test_no_forward_returns_empty():
    out = asyncio.run(expand_forwards(_B(None), Message("普通消息")))
    assert out == ""


def test_api_fail_silent():
    out = asyncio.run(expand_forwards(_B(fail=True), _msg_with_forward()))
    assert out == ""
