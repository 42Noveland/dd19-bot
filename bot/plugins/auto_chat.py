"""决策层：群聊主动接话（不@也会回复）。

流程（priority=48；仅 LLM_REPLY_MODE=mention 时生效，@ 消息仍由 llm_chat 处理）：
  廉价预筛（非@/非命令/非自己消息/每群冷却/刚说过话）→ 概率门（提到机器人名字则必判）
  → 异步 LLM 判断「接/不接」（宁缺毋滥）→ 复用聊天核心静默回复（带引用，可配表情包）。

设计要点：
- 判断与回复全在后台任务里跑，不阻塞事件处理；同一群同一时刻只判断一条（_inflight）。
- 判断材料 = 最近群聊记录 + 新消息（addressed=False 措辞）。
- 接话后设每群冷却（AUTO_REPLY_COOLDOWN 秒）；失败一律静默（不打扰群友）。
"""
from __future__ import annotations

import asyncio
import random
import time

from loguru import logger as _log
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
from nonebot.rule import Rule

from core import context, llm, personas
from core.config import get_config
from core.gate import is_allowed_group, mentions_name, parse_judge_verdict, render_message_text
from plugins._shared import resolve_at_names, sender_name

_tasks: set[asyncio.Task] = set()
_last_auto: dict[int, float] = {}
_inflight: set[int] = set()

_JUDGE_RULES = (
    "【任务】你是群聊成员「{name}」。下面给你群聊最近的记录和一条新消息。"
    "判断：这条新消息，你要不要主动接一句话？\n"
    "接话原则：\n"
    "- 大多数消息不用接。只有接话显得自然、有趣、有帮助，或有人在说你/问你/聊到你熟悉的话题时，才接。\n"
    "- 别人在互相聊天、内容平淡、话题与你无关、刚有人回应过、或你刚接过话的，不要接。\n"
    "- 拿不准就不接——宁可少接，不要打扰群友。\n"
    "输出：第一行只写「接」或「不接」，不要写别的；如愿意可在第二行给一句理由（仅供日志）。"
)

_AUTO_NOTE = (
    "【场合】这条新消息没有人 @ 你，是你自己主动想接一句。要像群里朋友随口插话："
    "一两句、自然、不突兀；不用客套和客服腔；没把握就少说。"
)


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


auto_reply = on_message(rule=Rule(_allowed), priority=48, block=False)


def _bot_names(cfg, group_id: int) -> list[str]:
    names: list[str] = []
    try:
        pname = personas.name_for(group_id)
    except Exception:  # noqa: BLE001
        pname = ""
    for n in [pname, str(cfg.bot_name or "").strip(), "dd19", "机器人"]:
        if n and n not in names:
            names.append(n)
    return names


def _bot_spoke_recently(group_id: int, self_id: str, window: float = 90.0) -> bool:
    """最近 window 秒内机器人说过话（回复/表情包）→ 本轮不抢话。"""
    now = time.time()
    for row in context.recent_messages(group_id, limit=8):
        if str(row.get("user_id")) == str(self_id):
            return now - float(row.get("ts") or 0.0) < window
    return False


@auto_reply.handle()
async def _consider(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = get_config()
    if not cfg.auto_reply_enabled or cfg.llm_reply_mode != "mention":
        return
    if event.is_tome():
        return  # @/引用机器人的消息交给 llm_chat 处理
    message = event.get_message()
    probe = render_message_text(message, str(bot.self_id))
    if not probe or probe.startswith("/"):
        return
    gid = event.group_id
    now = time.time()
    if gid in _inflight or now - _last_auto.get(gid, 0.0) < cfg.auto_reply_cooldown:
        return
    if _bot_spoke_recently(gid, str(bot.self_id)):
        return
    if not mentions_name(probe, _bot_names(cfg, gid)) and random.random() >= cfg.auto_reply_chance:
        return
    task = asyncio.create_task(_judge_and_maybe_reply(bot, event))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _judge_and_maybe_reply(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = get_config()
    gid = event.group_id
    _inflight.add(gid)
    try:
        names = await resolve_at_names(bot, gid, event.get_message())
        text = render_message_text(event.get_message(), str(bot.self_id), names)
        if not text:
            return
        history = [
            row
            for row in context.recent_messages(gid, limit=cfg.llm_context_messages + 1)
            if str(row.get("message_id")) != str(event.message_id)
        ][-cfg.llm_context_messages :]
        material = context.format_context_prompt(
            history, sender_name(event), text, addressed=False, self_qq=int(event.self_id)
        )
        pname, ptext = personas.resolve(gid)
        verdict = await llm.chat_once(
            material,
            cfg.providers[llm.current_default()],
            session_key=f"qqbot-judge-{gid}",
            extra_system=_JUDGE_RULES.replace("{name}", pname),
            system_prompt_override=ptext,
        )
        raw = (verdict.text or "").strip()
        ok = parse_judge_verdict(raw)
        _log.info(
            "auto judge [{}] {} | {} | {}",
            gid,
            "接" if ok else "不接",
            text[:50],
            raw.replace("\n", " ")[:80],
        )
        if not ok:
            return
        _last_auto[gid] = time.time()
        from plugins.llm_chat import chat_flow  # 局部导入：避免插件加载顺序耦合

        async def _send(msg):
            await bot.send(event, msg)

        await chat_flow(bot, event, text, _send, quiet_skip=True, extra_note=_AUTO_NOTE, addressed=False)
    except llm.QuotaExceededError:
        return
    except Exception as exc:  # noqa: BLE001 —— 主动接话失败保持安静
        _log.opt(exception=True).warning("auto reply failed [{}]: {}: {}", gid, type(exc).__name__, exc)
    finally:
        _inflight.discard(gid)
