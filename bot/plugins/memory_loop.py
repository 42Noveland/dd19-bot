"""记忆系统后台循环 + /memory 管理命令（仅管理员）。

- 循环：每 MEMORY_TICK 秒检查各白名单群；积压 ≥10 条才提炼，每轮每群最多 3 批（追平时自动限速）。
- 命令：/memory 查看统计；/memory extract [群号] 立即提炼（用于手动补课/调试）。
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

from core import llm, memory
from core.config import get_config
from core.gate import is_allowed_group

_MIN_PENDING = 10  # 积压不足这个数就先不提炼（省 token）
_extract_lock = asyncio.Lock()
_tick_task: asyncio.Task | None = None

driver = nonebot.get_driver()


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


mem_cmd = on_command("memory", rule=Rule(_allowed), permission=SUPERUSER, priority=20, block=True)


async def _loop() -> None:
    await asyncio.sleep(20)  # 启动缓冲：等连接稳定
    while True:
        cfg = get_config()
        if cfg.memory_enabled:
            for gid in sorted(cfg.allowed_group_ids):
                try:
                    if memory.pending_count(gid) < _MIN_PENDING:
                        continue
                    async with _extract_lock:
                        done = 0
                        while done < 3:
                            processed, added = await memory.extract_group(gid, cfg)
                            if processed <= 0:
                                break
                            done += 1
                            _log.info("memory extract [{}]: 处理 {} 条，新增 {} 条", gid, processed, added)
                            if processed < cfg.memory_batch:
                                break
                except llm.QuotaExceededError:
                    _log.warning("memory extract 跳过：今日额度已用完")
                    break
                except Exception as exc:  # noqa: BLE001 —— 提炼失败不影响其他功能
                    _log.warning("memory extract failed [{}]: {}", gid, exc)
        await asyncio.sleep(cfg.memory_tick)


@driver.on_startup
async def _start() -> None:
    global _tick_task
    _tick_task = asyncio.create_task(_loop())


@mem_cmd.handle()
async def _handle_memory(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    cfg = get_config()
    parts = args.extract_plain_text().strip().split()
    if parts and parts[0] == "extract":
        gid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else int(event.group_id)
        processed = added = 0
        try:
            async with _extract_lock:
                for _ in range(3):
                    p, a = await memory.extract_group(gid, cfg)
                    processed += p
                    added += a
                    if p < cfg.memory_batch:
                        break
        except llm.QuotaExceededError:
            await mem_cmd.finish(cfg.llm_quota_reply)
            return
        except Exception as exc:  # noqa: BLE001
            await mem_cmd.finish(f"提炼失败：{type(exc).__name__}（{exc}）")
            return
        st = memory.stats_for(gid)
        await mem_cmd.finish(
            f"提炼完成：处理 {processed} 条消息，新增 {added} 条记忆；"
            f"本群现有 成员事实 {st['user']} 条 / 群事件 {st['group']} 条；待处理 {st['pending']} 条"
        )
    st = memory.stats_for(event.group_id)
    await mem_cmd.finish(
        f"记忆库（本群）：成员事实 {st['user']} 条 / 群事件 {st['group']} 条；待处理 {st['pending']} 条\n"
        "用法：/memory extract [群号] 立即提炼"
    )
