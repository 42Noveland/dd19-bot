from core.config import Config
from core.gate import is_allowed_group, should_reply_plain, strip_text_mention


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
