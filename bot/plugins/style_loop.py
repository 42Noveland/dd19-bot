"""风格学习后台循环 + /style 管理命令（仅管理员）。

- 循环：每 STYLE_TICK 秒对每个白名单群增量提取「用户→Bot」风格对（纯本地，零 LLM 成本）。
- 命令：/style 查看统计；/style extract 立即提取。
"""
from __future__ import annotations

import asyncio

import nonebot
from loguru import logger as _log
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from core import style_pairs
from core.config import get_config
from core.gate import is_allowed_group

_tick_task: asyncio.Task | None = None

driver = nonebot.get_driver()


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


style_cmd = on_command("style", rule=Rule(_allowed), permission=SUPERUSER, priority=20, block=True)


async def _loop() -> None:
    await asyncio.sleep(25)  # 启动缓冲：等连接稳定
    while True:
        cfg = get_config()
        if cfg.style_enabled:
            for gid in sorted(cfg.allowed_group_ids):
                try:
                    processed, saved = style_pairs.extract_group(gid, cfg)
                    if saved:
                        _log.info("style extract [{}]: 处理 {} 行，新增/刷新 {} 对", gid, processed, saved)
                except Exception as exc:  # noqa: BLE001 —— 风格提取失败不影响其他功能
                    _log.warning("style extract failed [{}]: {}", gid, exc)
        await asyncio.sleep(cfg.style_tick)


@driver.on_startup
async def _start() -> None:
    global _tick_task
    _tick_task = asyncio.create_task(_loop())


@style_cmd.handle()
async def _handle_style(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    cfg = get_config()
    gid = int(event.group_id)
    parts = args.extract_plain_text().strip().split()
    if parts and parts[0] == "extract":
        try:
            processed, saved = style_pairs.extract_group(gid, cfg)
        except Exception as exc:  # noqa: BLE001
            await style_cmd.finish(f"提取失败：{type(exc).__name__}（{exc}）")
            return
        st = style_pairs.stats_for(gid)
        await style_cmd.finish(f"提取完成：处理 {processed} 行，新增/刷新 {saved} 对；本群现有 {st['pairs']} 对")
    st = style_pairs.stats_for(gid)
    await style_cmd.finish(
        f"风格库（本群）：{st['pairs']} 对（已处理到消息行 {st['watermark']}）\n"
        "用法：/style extract 立即提取；风格对会在回复时自动注入相似场合的样例"
    )
