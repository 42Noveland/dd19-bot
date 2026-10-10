"""插件共享小工具（以 _ 开头：不会被 nonebot.load_plugins 当插件加载）。"""
from __future__ import annotations

import time

from nonebot.adapters.onebot.v11 import Bot, Message

_AT_NAME_CACHE: dict[tuple[int, str], str] = {}


async def resolve_at_names(bot: Bot, group_id: int, message: Message) -> dict[str, str]:
    """为消息里的 @ 段解析昵称（带进程级缓存；失败退化为 QQ 号）。"""
    names: dict[str, str] = {}
    for seg in message:
        if seg.type != "at":
            continue
        qq = str(seg.data.get("qq", ""))
        if not qq or qq == "all" or qq == str(bot.self_id):
            continue
        key = (group_id, qq)
        if key not in _AT_NAME_CACHE:
            name = qq
            try:
                info = await bot.get_group_member_info(group_id=group_id, user_id=int(qq))
                name = str(info.get("card") or info.get("nickname") or "").strip() or qq
            except Exception:  # noqa: BLE001 —— 解析失败不阻塞消息处理
                pass
            _AT_NAME_CACHE[key] = name
        names[qq] = _AT_NAME_CACHE[key]
    return names


def sender_name(event) -> str:
    """群消息发送者的展示名：群名片 > 昵称 > QQ 号。"""
    sender = getattr(event, "sender", None)
    card = str(getattr(sender, "card", "") or "").strip()
    nick = str(getattr(sender, "nickname", "") or "").strip()
    return card or nick or str(getattr(event, "user_id", "?"))


# ---------------- 跟聊窗口（per-sender：谁被回复谁进窗口） ----------------
# 机制参考 qq-agent 的 short-followup：bot 成功回复某人后开窗，窗口内该人的
# 后续消息跳过概率门必判——"聊到一半不理人"发生在对话对手身上，不是全群；
# 别人插话不受影响（仍走正常概率门）。
# 内存态、重启清空（窗口只有几分钟，无需持久化）。

FOLLOWUP_WINDOW = 120.0  # 秒

_followups: dict[tuple[int, int], float] = {}


def note_replied(group_id: int, user_id: int, window: float = FOLLOWUP_WINDOW) -> None:
    """bot 成功回复了谁：为 (群, 人) 开/续跟聊窗口。"""
    try:
        key = (int(group_id), int(user_id))
    except (TypeError, ValueError):
        return
    _followups[key] = time.time() + float(window)
    if len(_followups) > 2000:  # 防无限增长：清掉已过期的
        now = time.time()
        for k in [k for k, v in _followups.items() if v < now]:
            _followups.pop(k, None)


def in_followup(group_id: int, user_id: int) -> bool:
    """该人是否处在跟聊窗口内（窗口内其消息跳过概率门必判）。"""
    try:
        key = (int(group_id), int(user_id))
    except (TypeError, ValueError):
        return False
    return time.time() < _followups.get(key, 0.0)


def clear_followups() -> None:
    """清空全部窗口（测试/重置用）。"""
    _followups.clear()


def _plain_seg(seg) -> str:
    """把转发节点里的一条内容段（dict 或 MessageSegment）压成纯文本。"""
    if isinstance(seg, dict):
        st = str(seg.get("type") or "")
        sd = seg.get("data") or {}
        if st == "text":
            return str(sd.get("text") or "")
        if st == "at":
            return "@" + str(sd.get("qq") or "")
        if st == "image":
            return "[图片]"
        if st == "face":
            return "[表情]"
        return ""
    st = str(getattr(seg, "type", "") or "")
    try:
        if st == "text":
            return str(seg.data.get("text") or "")
        if st == "at":
            return "@" + str(seg.data.get("qq") or "")
        if st == "image":
            return "[图片]"
        if st == "face":
            return "[表情]"
    except Exception:  # noqa: BLE001
        return ""
    return ""


async def expand_forwards(bot: Bot, message: Message, *, max_items: int = 12, max_chars: int = 80) -> str:
    """把消息里的合并转发（forward 段）展开成文本（供记录/回复使用，#12）。

    NapCat/OB11：forward.data.id → get_forward_msg → nodes（昵称 + 内容段）。
    失败静默返回 ""（拿不到就不拿，不阻塞消息处理）；最多处理 2 个转发、每个 12 条。
    """
    ids: list[str] = []
    for seg in message:
        if getattr(seg, "type", "") == "forward":
            fid = str(seg.data.get("id") or "").strip()
            if fid:
                ids.append(fid)
    if not ids:
        return ""
    chunks: list[str] = []
    for fid in ids[:2]:
        try:
            data = await bot.call_api("get_forward_msg", message_id=fid)
        except Exception:  # noqa: BLE001 —— 拉不到转发内容就算了
            continue
        nodes = []
        if isinstance(data, dict):
            nodes = data.get("messages") or data.get("message") or []
        lines: list[str] = []
        for node in (nodes or [])[:max_items]:
            d = node
            if isinstance(node, dict) and node.get("type") == "node":
                d = node.get("data") or {}
            if not isinstance(d, dict):
                continue
            who = str(d.get("nickname") or d.get("user_id") or "").strip()
            content = d.get("content") or d.get("message") or []
            segs = content if isinstance(content, list) else [content]
            t = "".join(_plain_seg(s) for s in segs)
            t = " ".join(t.split())[:max_chars]
            if t:
                lines.append(f"{who}: {t}" if who else t)
        if lines:
            chunks.append("[转发记录]\n" + "\n".join(lines))
    return "\n".join(chunks)[:1200]
