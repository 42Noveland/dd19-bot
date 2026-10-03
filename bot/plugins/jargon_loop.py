"""黑话后台循环 + /jargon 管理命令（仅管理员）。

- 循环：每 JARGON_TICK 秒扫描各群最近消息，零 LLM 预筛出候选（频次达标且未入表）；
  有候选才调一次 LLM 批量推断含义（每轮最多 4 个）。
- 命令：/jargon 查看本群已知黑话；/jargon mine 立即挖掘一轮。
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

from core import jargon, llm
from core.config import get_config
from core.gate import is_allowed_group

_tick_task: asyncio.Task | None = None
_mine_lock = asyncio.Lock()

driver = nonebot.get_driver()


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


jargon_cmd = on_command("jargon", rule=Rule(_allowed), permission=SUPERUSER, priority=20, block=True)


async def _loop() -> None:
    await asyncio.sleep(30)  # 启动缓冲：等连接稳定
    while True:
        cfg = get_config()
        if cfg.jargon_enabled:
            for gid in sorted(cfg.allowed_group_ids):
                try:
                    async with _mine_lock:
                        r = await jargon.mine_round(gid, cfg)
                    if r["saved"]:
                        _log.info(
                            "jargon mine [{}]: 候选 {}，写入 {}，已知 {}",
                            gid, r["candidates"], r["saved"], r["known"],
                        )
                except llm.QuotaExceededError:
                    _log.warning("jargon mine 跳过：今日额度已用完")
                    break
                except Exception as exc:  # noqa: BLE001 —— 挖掘失败不影响其他功能
                    _log.warning("jargon mine failed [{}]: {}", gid, exc)
        await asyncio.sleep(cfg.jargon_tick)


@driver.on_startup
async def _start() -> None:
    global _tick_task
    _tick_task = asyncio.create_task(_loop())


@jargon_cmd.handle()
async def _handle_jargon(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    cfg = get_config()
    gid = int(event.group_id)
    parts = args.extract_plain_text().strip().split()
    if parts and parts[0] in ("mine", "挖掘"):
        try:
            async with _mine_lock:
                r = await jargon.mine_round(gid, cfg)
        except llm.QuotaExceededError:
            await jargon_cmd.finish(cfg.llm_quota_reply)
            return
        except Exception as exc:  # noqa: BLE001
            await jargon_cmd.finish(f"挖掘失败：{type(exc).__name__}（{exc}）")
            return
        await jargon_cmd.finish(f"挖掘完成：候选 {r['candidates']} 个，写入 {r['saved']} 个；本群已知黑话 {r['known']} 条")
    st = jargon.stats_for(gid)
    lines = [f"黑话库（本群）：已知 {st['known']} 条 / 已排除 {st['rejected']} 条"]
    for term, meaning in jargon.list_known(gid, limit=10):
        lines.append(f"- 「{term}」≈ {meaning}")
    lines.append("用法：/jargon mine 立即挖掘一轮（含黑话的消息会自动注入解释）")
    await jargon_cmd.finish("\n".join(lines))
