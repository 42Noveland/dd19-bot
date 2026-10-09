"""防抖聚批单测（#7）：同群同人短窗连发只处理末条（文本合并）；隔离与残留清理。"""
import asyncio

from core import debounce


def test_only_last_settles():
    debounce.reset()
    e1, e2 = object(), object()
    debounce.note(1, 100, "auto", e1, "在吗")
    debounce.note(1, 100, "auto", e2, "忙不忙")
    assert debounce.settle(1, 100, "auto", e1) is None  # 被更新的消息取代
    assert debounce.settle(1, 100, "auto", e2) == "在吗\n忙不忙"  # 末条带走合并文本
    assert debounce.settle(1, 100, "auto", e2) is None  # 已清窗


def test_users_and_kinds_isolated():
    debounce.reset()
    ea, eb, ec = object(), object(), object()
    debounce.note(1, 100, "auto", ea, "A")
    debounce.note(1, 200, "auto", eb, "B")  # 不同人
    debounce.note(1, 100, "direct", ec, "C")  # 不同通道
    assert debounce.settle(1, 100, "auto", ea) == "A"
    assert debounce.settle(1, 200, "auto", eb) == "B"
    assert debounce.settle(1, 100, "direct", ec) == "C"


def test_stale_window_reset(monkeypatch):
    debounce.reset()
    e1, e2 = object(), object()
    debounce.note(1, 100, "auto", e1, "旧消息")
    # 把窗口时间拨旧（模拟异常悬挂），新消息应重开窗口、不带旧文本
    k = (1, 100, "auto")
    debounce._windows[k]["ts"] -= 300
    debounce.note(1, 100, "auto", e2, "新消息")
    assert debounce.settle(1, 100, "auto", e2) == "新消息"


def test_merge_window_parallel(monkeypatch):
    """两条并行：先到的被取代（None），后到的带走合并文本。"""
    debounce.reset()
    monkeypatch.setattr(debounce, "WINDOW", 0.05)

    async def go():
        e1, e2 = object(), object()
        t1 = asyncio.create_task(debounce.merge_window("auto", 1, 100, e1, "在吗"))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(debounce.merge_window("auto", 1, 100, e2, "忙不忙"))
        return await asyncio.gather(t1, t2)

    r1, r2 = asyncio.run(go())
    assert r1 is None
    assert r2 == "在吗\n忙不忙"


def test_merge_window_single(monkeypatch):
    """单条：窗口到期后返回自身文本。"""
    debounce.reset()
    monkeypatch.setattr(debounce, "WINDOW", 0.01)
    r = asyncio.run(debounce.merge_window("direct", 1, 100, object(), "单独一条"))
    assert r == "单独一条"
