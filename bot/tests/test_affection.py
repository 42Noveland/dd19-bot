import time

from core import affection, context, mood


def test_classify():
    assert affection.classify("谢谢你呀")[0] == 2
    assert affection.classify("你好厉害啊")[0] == 5
    assert affection.classify("滚")[0] == -5
    assert affection.classify("去死吧")[0] == -10
    assert affection.classify("今天天气不错")[0] == 1


def test_update_basic_and_cap(tmp_path):
    context.reset(tmp_path / "ctx.db")
    r = affection.update(111, 222, "小红", "谢谢你呀")
    assert r["level"] == 2 and r["desc"] == "表达感谢"
    affection.set_level(111, 222, "小红", 99)
    r2 = affection.update(111, 222, "小红", "谢谢你")
    assert r2["level"] == 100  # 封顶
    r3 = affection.update(111, 333, "小郑", "滚开")  # 负面
    assert r3["level"] == 0  # 0 起步 -5 → 0


def test_redistribution(tmp_path):
    context.reset(tmp_path / "ctx.db")
    affection.set_level(111, 1, "A", 100)
    affection.set_level(111, 2, "B", 100)
    affection.set_level(111, 3, "C", 48)
    r = affection.update(111, 3, "C", "太厉害了")  # +5 → 总量超 250 → 从 A/B 扣
    assert r["level"] == 53
    assert affection.total(111) <= affection.CAP_TOTAL
    levels = {x["user_id"]: x["level"] for x in affection.top_list(111, 10)}
    assert levels[1] + levels[2] == 200 - 3  # 溢出 3 点被扣走


def test_mood_modifier(tmp_path):
    context.reset(tmp_path / "ctx.db")
    mood.set_mood(111, "开心", 1.0)
    r = affection.update(111, 222, "小红", "太厉害了")  # 5 × 1.2 = 6
    assert r["level"] == 6
    mood.set_mood(111, "恼火", 1.0)
    r2 = affection.update(111, 333, "小郑", "太厉害了")  # 5 × 0.5 = 2.5 → 2
    assert r2["level"] == 2


def test_passive_decay(tmp_path):
    context.reset(tmp_path / "ctx.db")
    affection.set_level(111, 222, "小红", 50)
    with context._lock:
        conn = context._db()
        conn.execute(
            "UPDATE affection SET last_ts=? WHERE group_id=111 AND user_id=222",
            (time.time() - 10 * 86400,),
        )
        conn.commit()
    r = affection.update(111, 222, "小红", "你好")  # +1，先扣 5（10 天 / 2）
    assert r["level"] == 46 and r["decayed"] == 5


def test_for_prompt(tmp_path):
    context.reset(tmp_path / "ctx.db")
    assert affection.for_prompt(111, 222, "小红") == ""
    affection.set_level(111, 222, "小红", 78)
    block = affection.for_prompt(111, 222, "小红")
    assert "78/100" in block and "小红" in block
    affection.set_level(111, 222, "小红", 5)
    assert "还不太熟" in affection.for_prompt(111, 222, "小红")
