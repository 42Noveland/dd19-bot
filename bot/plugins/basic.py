"""/ping /help /jrrp 基础指令（仅白名单群生效）。"""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent
from nonebot.rule import Rule

from core import jrrp
from core.config import get_config
from core.gate import is_allowed_group


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


def _help_text() -> str:
    cfg = get_config()
    if cfg.llm_reply_mode == "mention":
        chat_line = "@我 或 /chat <内容> 与 AI 聊天"
    elif cfg.llm_reply_mode == "all":
        chat_line = "直接说话或 /chat <内容> 与 AI 聊天"
    else:
        chat_line = "/chat <内容> 与 AI 聊天"
    return (
        f"我是 {cfg.bot_name}。可用指令：\n"
        "/ping 测试机器人是否在线\n"
        "/jrrp 今日人品值\n"
        f"{chat_line}\n"
        "/model 查看/切换模型（仅管理员）\n"
        "/help 显示本菜单"
    )


ping = on_command("ping", rule=Rule(_allowed), priority=10, block=True)
help_cmd = on_command("help", rule=Rule(_allowed), aliases={"帮助", "菜单"}, priority=10, block=True)
jrrp_cmd = on_command("jrrp", rule=Rule(_allowed), aliases={"人品"}, priority=10, block=True)


@ping.handle()
async def _handle_ping() -> None:
    await ping.finish("pong!")


@help_cmd.handle()
async def _handle_help() -> None:
    await help_cmd.finish(_help_text())


@jrrp_cmd.handle()
async def _handle_jrrp(event: GroupMessageEvent) -> None:
    value = jrrp.calc_jrrp(event.user_id, jrrp.today_str())
    await jrrp_cmd.finish(f"今日人品值：{value}（0-100）")
