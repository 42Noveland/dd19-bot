"""插件共享小工具（以 _ 开头：不会被 nonebot.load_plugins 当插件加载）。"""
from __future__ import annotations

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
