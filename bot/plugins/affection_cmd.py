"""好感度管理命令（仅管理员）：/affection 查看本群排行与总量；/affection set <QQ> <值> 设置。"""
from __future__ import annotations

import nonebot
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from core import affection
from core.gate import is_allowed_group

driver = nonebot.get_driver()


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


aff_cmd = on_command("affection", rule=Rule(_allowed), permission=SUPERUSER, priority=20, block=True)


@aff_cmd.handle()
async def _handle_affection(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    gid = int(event.group_id)
    parts = args.extract_plain_text().strip().split()
    if (
        len(parts) >= 3
        and parts[0] == "set"
        and parts[1].isdigit()
        and parts[2].lstrip("-").isdigit()
    ):
        lv = max(0, min(int(parts[2]), affection.CAP_PER_USER))
        affection.set_level(gid, int(parts[1]), "", lv)
        await aff_cmd.finish(f"已设置 {parts[1]} 的好感度为 {lv}")
    rows = affection.top_list(gid, limit=10)
    total = affection.total(gid)
    lines = [f"好感度（本群，总 {total}/{affection.CAP_TOTAL}）——夸赞+5/感谢+2/普通聊天+1，坏话扣分；久不聊会自然衰减："]
    for r in rows:
        lines.append(f"- {r['name'] or r['user_id']}：{r['level']}/100")
    if not rows:
        lines.append("（还没有记录——有人来聊天就有了）")
    lines.append("用法：/affection set <QQ> <值>（管理员）")
    await aff_cmd.finish("\n".join(lines))
