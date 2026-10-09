"""定时提醒循环（#13）：每 30 秒查到期提醒 → 群里 @ 发送 → 标记完成。

- 数据在 context.db（context.reminder_*），进程重启不丢；
- 发送用当前唯一 bot 连接（无连接时等下一轮）；发送失败不标完成，下轮重试；
- 工具端在 llm_chat（set_reminder）；管理命令 /remind（本人列表/取消）。
"""
from __future__ import annotations

import asyncio
import time

import nonebot
from loguru import logger as _log
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from core import context
from core.gate import is_allowed_group

_TICK = 30.0
_tick_task: asyncio.Task | None = None

driver = nonebot.get_driver()


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


async def deliver_due(bot=None) -> int:
    """把到期的提醒发出去（bot 可用时）；返回发送条数。"""
    rows = context.reminder_due()
    if not rows:
        return 0
    if bot is None:
        try:
            bot = nonebot.get_bot()
        except Exception:  # noqa: BLE001 —— 无连接：等下一轮
            return 0
    sent = 0
    for r in rows:
        try:
            msg = MessageSegment.at(int(r["user_id"])) + f" 到点了：{str(r['text'])}"
            await bot.send_group_msg(group_id=int(r["group_id"]), message=msg)
            context.reminder_mark_done(int(r["id"]))
            sent += 1
        except Exception as exc:  # noqa: BLE001 —— 发送失败不标完成：下轮重试
            _log.opt(exception=True).warning("reminder send failed #{}: {}", r.get("id"), exc)
    return sent


async def _loop() -> None:
    await asyncio.sleep(15)  # 启动缓冲：等连接稳定
    while True:
        try:
            n = await deliver_due()
            if n:
                _log.info("reminder delivered: {}", n)
        except Exception as exc:  # noqa: BLE001 —— 循环保活
            _log.opt(exception=True).warning("reminder loop error: {}", exc)
        await asyncio.sleep(_TICK)


@driver.on_startup
async def _start() -> None:
    global _tick_task
    _tick_task = asyncio.create_task(_loop())


remind_cmd = on_command("remind", rule=Rule(_allowed), priority=20, block=True)


@remind_cmd.handle()
async def _handle_remind(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    parts = args.extract_plain_text().strip().split()
    if parts and parts[0] == "cancel" and len(parts) > 1 and parts[1].isdigit():
        ok = context.reminder_cancel(int(parts[1]), event.group_id, event.user_id)
        await remind_cmd.finish("已取消 #%s" % parts[1] if ok else "没找到这条（只能取消自己的提醒）")
    rows = context.reminder_list(event.group_id, event.user_id)
    if not rows:
        await remind_cmd.finish("你还没有待提醒的事。想让 dd19 提醒你，直接说\u201cx 分钟后/几点提醒我 xxx\u201d就行")
    lines = []
    for r in rows:
        t = time.strftime("%m-%d %H:%M", time.localtime(float(r["remind_ts"])))
        lines.append(f"#{r['id']} {t} {str(r['text'])[:40]}")
    await remind_cmd.finish("待提醒：\n" + "\n".join(lines) + "\n（取消：/remind cancel <编号>）")
