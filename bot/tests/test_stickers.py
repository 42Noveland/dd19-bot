import time

import pytest

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


def test_pick_mood_synonym(tmp_path):
    """同义情绪词扩展：query "开心" 应能命中含 "高兴" 的图。"""
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "a.jpg", "一只猫高兴得直蹦")
    row = stickers.pick("今天真开心", 1)
    assert row is not None and row["md5"] == "a.jpg"


def test_chat_hint_mentions_cadence():
    hint = stickers.chat_hint()
    assert "send_sticker" in hint and "不用等" in hint


def test_library_summary(tmp_path):
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "a.jpg", "这是一张黑猫歪头震惊的表情包")
    _mk(tmp_path, "b.jpg", "一只虎斑猫被捏住生无可恋")
    out = stickers.library_summary()
    assert "黑猫歪头震惊" in out and out.startswith("1.")
    context.reset(tmp_path / "ctx2.db")
    assert stickers.library_summary() == ""


def _age_image(md5: str, days: float) -> None:
    """把图片的最后出现时间改老（测试衰减用）。"""
    with context._lock:
        conn = context._db()
        conn.execute("UPDATE images SET last_ts=? WHERE md5=?", (time.time() - days * 86400, md5))
        conn.commit()


def test_freshness_curve():
    now = time.time()
    assert stickers._freshness({"last_ts": now}) == pytest.approx(1.0)
    assert 0.7 < stickers._freshness({"last_ts": now - 7.5 * 86400}) < 0.9
    assert stickers._freshness({"last_ts": now - 20 * 86400}) == pytest.approx(0.2)
    assert stickers._freshness({}) == pytest.approx(0.2)


def test_pick_prefers_fresh_on_tie(tmp_path):
    """同样关键词命中时，新鲜图优先于 15 天没出现的旧图。"""
    context.reset(tmp_path / "ctx.db")
    stickers.reset()
    _mk(tmp_path, "old.jpg", "表达得意的坏笑")
    _mk(tmp_path, "new.jpg", "表达得意的坏笑")
    _age_image("old.jpg", 20)
    row = stickers.pick("得意", 1)
    assert row is not None and row["md5"] == "new.jpg"
