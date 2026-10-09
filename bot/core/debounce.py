"""消息防抖聚批（#7）：同群同人短窗连发 → 只处理末条，前面的并入文本。

用户连发多条短消息（"对了"/"明天"/"几点来着"）时，逐条处理显得机器人式
（每条都动念头/逐条回）。本模块把短窗内的连续消息聚成一批：

  - note()         每条到达时记账（进入窗口）
  - settle()       窗口到期裁决：只有"最后一条"胜出并带走合并文本；被更新的消息返回 None
  - merge_window() 处理侧便捷封装（note → sleep(WINDOW) → settle）

约定：
  - key 用事件对象本身（settle 传同一对象即"我是不是末条"）；
  - kind 区分处理通道（"direct" 直回 / "auto" 主动接话），互不影响；
  - 窗口超过 _STALE 秒未被结算视为残留，下次 note 自动丢弃重开（防异常悬挂）。
"""
from __future__ import annotations

import asyncio
import time

WINDOW = 2.0  # 防抖窗口（秒）：连续消息间隔小于它即视为一批
_STALE = 30.0  # 残留窗口清理：窗口这么久没被结算则丢弃重开

_windows: dict[tuple[int, int, str], dict] = {}


def note(group_id: int, user_id: int, kind: str, key, text: str) -> None:
    """把一条消息记入窗口（key=事件对象；text=该条的渲染文本）。"""
    k = (int(group_id), int(user_id), str(kind))
    now = time.time()
    w = _windows.get(k)
    if w is not None and now - float(w.get("ts") or 0.0) > _STALE:
        w = None  # 旧窗口悬挂太久：丢弃重开，防把上古文本并进来
    if w is None:
        w = {"texts": [], "key": key, "ts": now}
        _windows[k] = w
    w["texts"].append(str(text or ""))
    w["key"] = key
    w["ts"] = now


def settle(group_id: int, user_id: int, kind: str, key) -> str | None:
    """窗口裁决：末条返回合并文本并清窗；被更新的消息返回 None（让位给末条）。"""
    k = (int(group_id), int(user_id), str(kind))
    w = _windows.get(k)
    if w is None:
        return None
    if w.get("key") is not key:
        return None  # 我不是末条：让位（窗口留给末条结算）
    _windows.pop(k, None)
    texts = [t for t in w.get("texts") or [] if t]
    return "\n".join(texts) if texts else None


async def merge_window(kind: str, group_id: int, user_id: int, key, text: str) -> str | None:
    """处理侧入口：记账 → 等窗口 → 末条裁决。

    返回合并文本（本条是末条）或 None（被更新的消息取代、放弃处理）。
    """
    note(group_id, user_id, kind, key, text)
    await asyncio.sleep(WINDOW)
    return settle(group_id, user_id, kind, key)


def reset() -> None:
    """清空所有窗口（测试辅助）。"""
    _windows.clear()
