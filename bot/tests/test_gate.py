from core.config import Config
from core.gate import (
    is_allowed_group,
    mentions_name,
    parse_judge_verdict,
    render_message_text,
    should_reply_plain,
    strip_text_mention,
)


def test_allowed_group_only():
    cfg = Config(allowed_group_ids={111})
    assert is_allowed_group(111, cfg) is True
    assert is_allowed_group(222, cfg) is False
    assert is_allowed_group(None, cfg) is False


def test_should_reply_plain_all_mode():
    assert should_reply_plain("你好", False, "all") is True
    assert should_reply_plain("/ping", False, "all") is False   # 斜杠消息不算聊天
    assert should_reply_plain("   ", False, "all") is False


def test_should_reply_plain_mention_and_command_modes():
    assert should_reply_plain("你好", False, "mention") is False
    assert should_reply_plain("你好", True, "mention") is True
    assert should_reply_plain("你好", True, "command") is False


def test_strip_text_mention():
    aliases = {"dd19", "123456789"}
    # 命中：空格/标点/直接接中文/仅@/前导空白
    assert strip_text_mention("@dd19 你好", aliases) == ("你好", True)
    assert strip_text_mention("@123456789 /ping", aliases) == ("/ping", True)
    assert strip_text_mention("@dd19", aliases) == ("", True)
    assert strip_text_mention("@dd19你好", aliases) == ("你好", True)
    assert strip_text_mention("  @dd19，在吗", aliases) == ("在吗", True)
    # 不命中：别名后接字母数字、其他昵称、@在句中
    assert strip_text_mention("@dd190 你好", aliases) == ("@dd190 你好", False)
    assert strip_text_mention("@张三 你好", aliases) == ("@张三 你好", False)
    assert strip_text_mention("你好 @dd19", aliases) == ("你好 @dd19", False)
    assert strip_text_mention("普通消息", aliases) == ("普通消息", False)


class _Seg:
    """轻量段对象（鸭子类型，避免单测里引入 nonebot 适配器）。"""

    def __init__(self, type_: str, data: dict):
        self.type = type_
        self.data = data


def test_render_message_text_keeps_mid_at_visible():
    msg = [
        _Seg("at", {"qq": "123456789"}),
        _Seg("text", {"text": "我想问问"}),
        _Seg("at", {"qq": "1234567890"}),
        _Seg("text", {"text": "是个怎样的人"}),
    ]
    assert render_message_text(msg, "123456789", {"1234567890": "老王"}) == "我想问问@老王是个怎样的人"
    # 无昵称映射时退化为 QQ 号，但 @ 依然可见
    assert render_message_text(msg, "123456789") == "我想问问@1234567890是个怎样的人"


def test_render_message_text_strips_leading_self_at():
    msg = [_Seg("at", {"qq": "123456789"}), _Seg("text", {"text": " 你好 @dd19"})]
    assert render_message_text(msg, "123456789") == "你好 @dd19"


def test_render_message_text_at_all_and_plain():
    msg = [_Seg("at", {"qq": "123456789"}), _Seg("text", {"text": "hi "}), _Seg("at", {"qq": "all"})]
    assert render_message_text(msg, "123456789") == "hi @全体成员"
    assert render_message_text([_Seg("text", {"text": "普通消息"})], "123456789") == "普通消息"
    assert render_message_text([_Seg("at", {"qq": "123456789"})], "123456789") == ""


def test_mentions_name():
    assert mentions_name("dd19 你在吗", ["十九", "dd19"]) is True
    assert mentions_name("今天吃啥", ["十九", "dd19"]) is False
    assert mentions_name("", ["十九"]) is False


def test_parse_judge_verdict():
    assert parse_judge_verdict("接") is True
    assert parse_judge_verdict("接\n这条挺有意思") is True
    assert parse_judge_verdict("不接") is False
    assert parse_judge_verdict("不接，他们在私聊") is False
    assert parse_judge_verdict("") is False
    assert parse_judge_verdict("……") is False
