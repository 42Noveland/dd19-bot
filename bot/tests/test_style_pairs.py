import time

from core import context, style_pairs
from core.config import load_config

BOT_QQ = 123456789


def _cfg():
    return load_config({})


def test_extract_pairs_basic(tmp_path):
    context.reset(tmp_path / "ctx.db")
    gid = 111
    context.record_message(gid, 29991, "小红", "你们觉得新出的那个游戏怎么样？")
    context.record_message(gid, BOT_QQ, "十九", "我还没玩，不过看评价挺两极的，你玩了吗？")
    processed, saved = style_pairs.extract_group(gid, _cfg())
    assert processed == 2 and saved == 1
    rows = style_pairs.list_pairs(gid)
    assert len(rows) == 1
    assert rows[0]["situation"].startswith("你们觉得")
    assert rows[0]["expression"].startswith("我还没玩")
    assert rows[0]["persona"] == "十九"


def test_extract_dedup_and_refresh(tmp_path):
    context.reset(tmp_path / "ctx.db")
    gid = 111
    context.record_message(gid, 29991, "小红", "今天吃啥好呢")
    context.record_message(gid, BOT_QQ, "十九", "随便整点，别纠结")
    style_pairs.extract_group(gid, _cfg())
    first = style_pairs.list_pairs(gid)[0]
    time.sleep(0.02)
    context.record_message(gid, 29992, "小郑", "今天吃啥好呢")
    context.record_message(gid, BOT_QQ, "十九", "随便整点，别纠结")
    processed, saved = style_pairs.extract_group(gid, _cfg())
    assert processed == 2 and saved == 1  # 复发：刷新而不是新增
    rows = style_pairs.list_pairs(gid)
    assert len(rows) == 1
    assert rows[0]["last_active_ts"] > first["last_active_ts"]


def test_extract_filters(tmp_path):
    context.reset(tmp_path / "ctx.db")
    gid = 111
    # 命令开头的情景：不学
    context.record_message(gid, 29991, "小红", "/ping")
    context.record_message(gid, BOT_QQ, "十九", "pong！在线呢")
    # 过短回复：不学
    context.record_message(gid, 29991, "小红", "在吗在吗")
    context.record_message(gid, BOT_QQ, "十九", "在")
    # 贴图行不打断配对：图+文两条回复仍配到同一条用户消息
    context.record_message(gid, 29991, "小红", "发个图乐呵乐呵")
    context.record_message(gid, BOT_QQ, "十九", "[表情包: 一只猫大笑]")
    context.record_message(gid, BOT_QQ, "十九", "喏，笑一个给你看看")
    processed, saved = style_pairs.extract_group(gid, _cfg())
    assert saved == 1
    rows = style_pairs.list_pairs(gid)
    assert rows[0]["situation"] == "发个图乐呵乐呵"
    assert rows[0]["expression"] == "喏，笑一个给你看看"


def test_extract_ignores_probe_bot_rows(tmp_path):
    context.reset(tmp_path / "ctx.db")
    gid = 111
    context.record_message(gid, 29991, "小红", "你好呀")
    context.record_message(gid, 10011, "十九", "你好，我在呢")  # 假 self_id（探针）：不学
    processed, saved = style_pairs.extract_group(gid, _cfg())
    assert saved == 0


def test_for_prompt_matching(tmp_path):
    context.reset(tmp_path / "ctx.db")
    gid = 111
    context.record_message(gid, 29991, "小红", "今天吃啥好呢")
    context.record_message(gid, BOT_QQ, "十九", "随便整点，别纠结")
    context.record_message(gid, 29992, "小郑", "外面天气不错啊")
    context.record_message(gid, BOT_QQ, "十九", "是挺舒服的，适合出去走走")
    style_pairs.extract_group(gid, _cfg())
    block = style_pairs.for_prompt(gid, "晚上吃啥", persona="十九")
    assert "吃啥" in block and "随便整点" in block
    assert "天气" not in block  # 相似度 0 的不注入
    assert style_pairs.for_prompt(gid, "完全不相关xyz", persona="十九") == ""
    assert style_pairs.for_prompt(gid, "晚上吃啥", persona="Elena") == ""  # 人设不匹配


def test_prune_old(tmp_path):
    context.reset(tmp_path / "ctx.db")
    gid = 111
    context.record_message(gid, 29991, "小红", "老对话一二三")
    context.record_message(gid, BOT_QQ, "十九", "老回复四五六")
    style_pairs.extract_group(gid, _cfg())
    with context._lock:
        conn = context._db()
        conn.execute("UPDATE style_pairs SET last_active_ts=?", (time.time() - 40 * 86400,))
        conn.commit()
    style_pairs.prune(gid)
    assert style_pairs.list_pairs(gid) == []
