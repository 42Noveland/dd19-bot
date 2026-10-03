"""情绪状态后台循环 + /mood 管理命令（仅管理员）。

- 循环：每 MOOD_TICK 秒检查各群；最近有新互动（≥6 条消息）才整理一次心情。
- 命令：/mood 查看；/mood update 立即整理；/mood set <心情> <强度> <缘由> 手动设置（调试/把玩）。
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

from core import llm, mood
from core.config import get_config
from core.gate import is_allowed_group

_tick_task: asyncio.Task | None = None

driver = nonebot.get_driver()


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


mood_cmd = on_command("mood", rule=Rule(_allowed), permission=SUPERUSER, priority=20, block=True)


async def _loop() -> None:
    await asyncio.sleep(30)  # 启动缓冲
    while True:
        cfg = get_config()
        if cfg.mood_enabled:
            for gid in sorted(cfg.allowed_group_ids):
                try:
                    ok = await mood.update_group(gid, cfg)
                    if ok:
                        m = mood.get_mood(gid)
                        if m:
                            _log.info(
                                "mood updated [{}]: {} ({:.2f}) {}", gid, m["mood"], m["intensity"], m["reason"]
                            )
                except llm.QuotaExceededError:
                    _log.warning("mood update 跳过：今日额度已用完")
                    break
                except Exception as exc:  # noqa: BLE001
                    _log.opt(exception=True).warning("mood update failed [{}]: {}: {}", gid, type(exc).__name__, exc)
        await asyncio.sleep(cfg.mood_tick)


@driver.on_startup
async def _start() -> None:
    global _tick_task
    _tick_task = asyncio.create_task(_loop())


@mood_cmd.handle()
async def _handle_mood(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    cfg = get_config()
    parts = args.extract_plain_text().strip().split(maxsplit=3)
    gid = int(event.group_id)
    if parts and parts[0] == "update":
        try:
            ok = await mood.update_group(gid, cfg)
        except llm.QuotaExceededError:
            await mood_cmd.finish(cfg.llm_quota_reply)
            return
        except Exception as exc:  # noqa: BLE001
            await mood_cmd.finish(f"整理失败：{type(exc).__name__}（{exc}）")
            return
        if not ok:
            await mood_cmd.finish("最近互动太少（或解析失败），心情保持不变")
            return
    elif parts and parts[0] == "set" and len(parts) >= 2:
        name = parts[1]
        if name not in mood.MOODS:
            await mood_cmd.finish("可选心情：" + " / ".join(mood.MOODS))
            return
        try:
            intensity = float(parts[2]) if len(parts) > 2 else 0.7
        except Exception:  # noqa: BLE001
            intensity = 0.7
        reason = parts[3] if len(parts) > 3 else "手动设置"
        mood.set_mood(gid, name, intensity, reason)
        await mood_cmd.finish(f"已设置心情：{name}（{intensity:.1f}）")
        return
    m = mood.get_mood(gid)
    if not m:
        await mood_cmd.finish(
            "当前心情：平静（默认）\n用法：/mood set <心情> <强度> <缘由>；/mood update 立即整理"
        )
        return
    await mood_cmd.finish(
        f"当前心情：{m['mood']}（强度 {m['intensity']:.2f}，约剩 {m['remain_min']:.0f} 分钟）"
        + (f"；缘由：{m['reason']}" if m["reason"] else "")
        + "\n用法：/mood set <心情> <强度> <缘由>；/mood update 立即整理"
    )
