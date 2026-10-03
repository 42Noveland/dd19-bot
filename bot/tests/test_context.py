from core import context


def _fresh(tmp_path):
    context.reset(tmp_path / "ctx.db")


def test_record_and_recent(tmp_path):
    _fresh(tmp_path)
    context.record_message(1, 100, "甲", "你好", message_id=10)
    context.record_message(1, 200, "乙", "在吗", message_id=11)
    context.record_message(2, 100, "甲", "别群消息", message_id=12)
    rows = context.recent_messages(1, limit=10)
    assert [r["text"] for r in rows] == ["你好", "在吗"]
    assert rows[0]["name"] == "甲"
    assert context.message_exists(1, 10) is True
    assert context.message_exists(1, 99) is False
    assert context.message_exists(2, 12) is True


def test_update_and_media(tmp_path):
    _fresh(tmp_path)
    rid = context.record_message(1, 100, "甲", "[图片]", message_id=10, media=[{"type": "image", "url": "http://x"}])
    row = context.get_message(rid)
    assert "http://x" in row["media"]
    context.update_message_text(rid, "[图片: 一只猫]")
    assert context.get_message(rid)["text"] == "[图片: 一只猫]"


def test_image_cache_lifecycle(tmp_path):
    _fresh(tmp_path)
    assert context.image_get("m1") is None
    context.image_upsert("m1", "/tmp/a.jpg", "一只猫", "cloud")
    got = context.image_get("m1")
    assert got["caption"] == "一只猫" and got["seen_count"] == 1
    context.image_touch("m1")
    assert context.image_get("m1")["seen_count"] == 2
    # 已有描述时，空描述 upsert 不覆盖；重复看到继续累计
    context.image_upsert("m1", "/tmp/a.jpg", "", "")
    got = context.image_get("m1")
    assert got["caption"] == "一只猫" and got["seen_count"] == 3


def test_format_context_prompt():
    hist = [{"name": "甲", "text": "下午吃什么"}, {"name": "乙", "text": "随便"}, {"name": "丙", "text": ""}]
    out = context.format_context_prompt(hist, "丁", "你们决定了吗")
    assert "甲: 下午吃什么" in out
    assert "乙: 随便" in out
    assert "丙: " not in out  # 空内容行被跳过
    assert "丁 对你说" in out and "你们决定了吗" in out
    # 无历史时退化为简单格式
    assert context.format_context_prompt([], "丁", "在吗") == "丁 对你说：在吗"


def test_format_context_prompt_not_addressed():
    out = context.format_context_prompt([], "小明", "今天吃啥", addressed=False)
    assert "对你说" not in out and "小明" in out and "今天吃啥" in out
    out2 = context.format_context_prompt([{"name": "A", "text": "hi"}], "小明", "今天吃啥", addressed=False)
    assert "对你说" not in out2 and "没有人 @ 你" in out2


def test_format_context_prompt_memories():
    out = context.format_context_prompt([], "小明", "hi", memories="【你记得的事】\n关于 小明：喜欢蓝色")
    assert "喜欢蓝色" in out and "对你说" in out
    out2 = context.format_context_prompt(
        [{"name": "A", "text": "x"}], "小明", "hi", memories="【你记得的事】"
    )
    assert "【你记得的事】" in out2 and "【群聊背景" in out2
