"""人格演化后台循环：定期为活跃群生成"人设补充建议"（进待审队列，管理员批准才生效）。

- 触发条件：距上次生成以来新增风格对 ≥ MIN_NEW_PAIRS（没人聊就不打扰）。
- 生成只入待审队列，绝不自动生效；批准/拒绝/回滚走 /persona 子命令。
"""
from __future__ import annotations

import asyncio

import nonebot
from loguru import logger as _log

from core import llm, persona_evo
from core.config import get_config

_tick_task: asyncio.Task | None = None

driver = nonebot.get_driver()


async def _loop() -> None:
    await asyncio.sleep(60)  # 启动缓冲：等连接稳定
    while True:
        cfg = get_config()
        if cfg.persona_evo_enabled:
            for gid in sorted(cfg.allowed_group_ids):
                try:
                    if persona_evo.new_pairs_since_last(gid) < persona_evo.MIN_NEW_PAIRS:
                        continue
                    r = await persona_evo.generate_proposals(gid, cfg)
                    if r["generated"]:
                        _log.info("persona evo [{}]: 新增待审 {} 条", gid, r["generated"])
                except llm.QuotaExceededError:
                    _log.warning("persona evo 跳过：今日额度已用完")
                    break
                except Exception as exc:  # noqa: BLE001 —— 生成失败不影响其他功能
                    _log.warning("persona evo failed [{}]: {}", gid, exc)
        await asyncio.sleep(cfg.persona_evo_tick)


@driver.on_startup
async def _start() -> None:
    global _tick_task
    _tick_task = asyncio.create_task(_loop())
