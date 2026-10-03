from core import context, stickers


def _mk(tmp_path, name: str, caption: str, seen: int = 1) -> None:
    f = tmp_path / name
    f.write_bytes(b"x" * 16)
    context.image_upsert(name, str(f), caption, "test")
    for _ in range(seen - 1):
        context.image_touch(name)


def test_pick_matches_keywords(tmp_path):
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "a.jpg", "一只猫竖起大拇指，表达赞同、点赞", seen=3)
    _mk(tmp_path, "b.jpg", "动漫女仆半睁眼坏笑，表达得意、心动")
    _mk(tmp_path, "c.jpg", "一张风景照片，蓝天白云")
    row = stickers.pick("得意", 1)
    assert row is not None and row["md5"] == "b.jpg"


def test_pick_no_match_still_sends(tmp_path):
    """完全没有关键词匹配：也发一张（模型既然要发，就发）。"""
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "a.jpg", "一只猫竖起大拇指")
    row = stickers.pick("火箭发射倒计时", 1)
    assert row is not None and row["md5"] == "a.jpg"


def test_pick_avoids_recent_repeat(tmp_path):
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "a.jpg", "表达得意的大笑", seen=2)
    _mk(tmp_path, "b.jpg", "表达得意的坏笑")
    first = stickers.pick("得意", 1)
    assert first is not None
    stickers.note_sent(1, first["md5"])
    second = stickers.pick("得意", 1)
    assert second is not None and second["md5"] != first["md5"]  # 优先换一张
    stickers.note_sent(1, second["md5"])
    third = stickers.pick("得意", 1)
    assert third is not None  # 全被防重排除（小图库）：兜底允许重发


def test_pick_repeat_allows_recent(tmp_path):
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "b.jpg", "动漫女仆坏笑，表达得意")
    row = stickers.pick("得意", 1)
    assert row is not None
    stickers.note_sent(1, row["md5"])
    again = stickers.pick("得意", 1, repeat=True)
    assert again is not None and again["md5"] == "b.jpg"


def test_pick_skips_missing_files(tmp_path):
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "gone.jpg", "表达得意的女仆")
    (tmp_path / "gone.jpg").unlink()
    assert stickers.pick("得意", 1) is None


def test_tool_spec_shape():
    spec = stickers.tool_spec()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "send_sticker"
    assert "query" in spec["function"]["parameters"]["properties"]
    assert "repeat" in spec["function"]["parameters"]["properties"]
