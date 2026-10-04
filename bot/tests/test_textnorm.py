from core import textnorm


def test_removes_chinese_bracket_narration():
    assert textnorm.clean_reply("（配了一张治愈系的小图，算是打个招呼）") == "呃呃"
    assert textnorm.clean_reply("嗯……你好呀（笑）") == "嗯……你好呀"
    assert textnorm.clean_reply("他说（大概）是这样") == "他说是这样"
    assert textnorm.clean_reply("【叹气】行吧") == "行吧"


def test_keeps_english_brackets():
    assert textnorm.clean_reply("版本 (v1.2) 发布了") == "版本 (v1.2) 发布了"
    assert textnorm.clean_reply("(hhh) 笑死我了") == "(hhh) 笑死我了"


def test_plain_and_multiline():
    assert textnorm.clean_reply("没有括号的普通回复") == "没有括号的普通回复"
    assert textnorm.clean_reply("第一段（旁白）\n\n\n\n第二段") == "第一段\n\n第二段"


def test_empty_input():
    assert textnorm.clean_reply("") == ""
