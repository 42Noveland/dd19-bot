"""消息观察者（感知层）：记录白名单群的全部消息到上下文库；图片异步识别回填。

- priority=45：先于默认聊天（50）执行，保证"触发消息"先入库（回复组装时可排除它）。
- 图片：立即记 "[图片]" 占位并启动异步任务（下载→云 vision 描述→回填文本）。
- 失败全部静默降级：记录失败/识别失败都不影响聊天本身。
"""
from __future__ import annotations

import asyncio

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
from nonebot.rule import Rule

from core import context, vision
from core.config import get_config
from core.gate import is_allowed_group, render_message_text
from plugins._shared import resolve_at_names, sender_name

_tasks: set[asyncio.Task] = set()


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


observer = on_message(rule=Rule(_allowed), priority=45, block=False)


@observer.handle()
async def _record(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = get_config()
    if event.user_id == int(bot.self_id):
        return  # 机器人自己的消息在回复时记录，避免重复
    message = event.get_message()
    media: list[dict] = []
    for seg in message:
        if seg.type == "image":
            url = str(seg.data.get("url") or "").strip()
            if url:
                media.append({"type": "image", "url": url})
    names = await resolve_at_names(bot, event.group_id, message)
    text = render_message_text(message, str(bot.self_id), names)
    if media:
        suffix = " ".join("[图片]" for _ in media)
        text = f"{text} {suffix}".strip() if text else suffix
    if not text:
        return
    if context.message_exists(event.group_id, event.message_id):
        return  # 重复投递去重
    row_id = context.record_message(
        event.group_id,
        event.user_id,
        sender_name(event),
        text,
        message_id=event.message_id,
        media=media or None,
    )
    if media and cfg.vision_enabled:
        task = asyncio.create_task(_caption_later(row_id, str(media[0]["url"]), cfg))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _caption_later(row_id: int, url: str, cfg) -> None:
    """异步识别图片并把描述回填到消息文本（替换第一个 "[图片]" 占位）。"""
    try:
        caption = await vision.describe(url, cfg)
    except Exception:  # noqa: BLE001
        return
    if not caption:
        return
    row = context.get_message(row_id)
    if not row:
        return
    new_text = str(row["text"]).replace("[图片]", f"[图片: {caption}]", 1)
    context.update_message_text(row_id, new_text)
