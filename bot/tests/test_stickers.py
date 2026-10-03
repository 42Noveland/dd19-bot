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


def test_pick_no_match_returns_none(tmp_path):
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "a.jpg", "一只猫竖起大拇指")
    assert stickers.pick("火箭发射倒计时", 1) is None


def test_pick_avoids_recent_repeat(tmp_path):
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "b.jpg", "动漫女仆坏笑，表达得意")
    row = stickers.pick("得意", 1)
    assert row is not None
    stickers.note_sent(1, row["md5"])
    assert stickers.pick("得意", 1) is None  # 刚发过：不重复


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
