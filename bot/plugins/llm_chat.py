"""/chat 指令 + 默认聊天（@我即聊，LLM_REPLY_MODE 可调）+ /model 管理（仅管理员）。

默认聊天（LLM_REPLY_MODE=mention，默认值）：在群里 @机器人（或引用回复机器人消息）
即可直接对话，无需 /chat 前缀；LLM_REPLY_MODE=all 时所有非命令消息都会交给 AI；
LLM_REPLY_MODE=command 时仅 /chat 指令触发。斜杠开头的消息一律不按聊天处理。
"""
import time
from pathlib import Path

from loguru import logger as _log
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment
from nonebot.message import event_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from core import affection, budget, context, jargon, llm, memory, mood, persona_evo, personas, search, stickers, style_pairs, textnorm

# 反 AI 味说话规则库（在 MaiBot「回复纪律」基础上扩充；机制借鉴 QQ-agent / qq-bridge 的
# 「仿真群友」设计，文案原创、适配「十九」人设）。走 extra_system：system 稳定区、保前缀缓存。
_CHAT_RULES = (
    "【说话规则】\n"
    "1. 你是群里的朋友，不是客服也不是助手，不用有求必应；没被点到的消息可以少接，"
    "被点到时也不用答得面面俱到——没话说就停，宁缺毋滥。\n"
    "2. 不用把每条消息都当任务完成。随口接一句、反问一下、先聊点别的，甚至不接，"
    "都比认真凑一整套答案自然；一次回一个话题就够了。\n"
    "3. 不总结大家的发言，不点评每个人的观点，不硬把话题拉回来；群聊不是开会，你不是主持人。\n"
    "4. 不知道就直说不知道，不感兴趣也可以让人看出来；不用为了显得周到硬找话说。\n"
    "5. 不用每句都客气、周到；熟人之间可以直来直去，别每条回复都带反问或关心人的话。\n"
    "6. 聊天别写小作文：一条消息顺着一口气说完，不分段、不换行、不先总后分、不列一二三（对方明确要清单除外）。\n"
    "7. 长度拿捏：平时聊天一两句、几十字就够；被问正经问题可以说清楚些，但也用聊天的口气、别铺长篇。"
    "偶尔回得很短很正常——“嗯”“哈哈”“？”“行吧”这种就行。\n"
    "8. 用过搜索就直接说结果，别汇报“我查了一下”“根据搜索”这类过程。\n"
    "9. 少用“绝对”“保证”“强烈推荐”这类夸张词，也别用总结式的收尾话。\n"
)
from core.config import get_config, normalize_provider_name
from core.gate import is_allowed_group, render_message_text, should_reply_plain, strip_text_mention
from plugins._shared import resolve_at_names, sender_name


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


# 默认聊天：适配器会把开头/结尾的 @机器人 剥离并置 event.to_me（引用回复机器人同样算）
chat_all = on_message(rule=Rule(_allowed), priority=50, block=False)
chat = on_command("chat", rule=Rule(_allowed), priority=20, block=True)
model_cmd = on_command("model", rule=Rule(_allowed), permission=SUPERUSER, priority=5, block=True)
persona_cmd = on_command("persona", rule=Rule(_allowed), permission=SUPERUSER, priority=5, block=True)
search_cmd = on_command("search", rule=Rule(_allowed), priority=20, block=True)
usage_cmd = on_command("usage", rule=Rule(_allowed), aliases={"额度"}, priority=20, block=True)

_MENTION_ALIASES: dict[str, set[str]] = {}


async def _bot_aliases(bot: Bot) -> set[str]:
    """机器人别名集合（QQ 号 + 登录昵称）；获取失败时退化为仅 QQ 号。"""
    key = str(bot.self_id)
    if key not in _MENTION_ALIASES:
        aliases = {key}
        try:
            info = await bot.get_login_info()
            nickname = str(info.get("nickname") or "").strip()
            if nickname:
                aliases.add(nickname)
        except Exception:  # noqa: BLE001
            pass
        _MENTION_ALIASES[key] = aliases
    return _MENTION_ALIASES[key]


@event_preprocessor
async def _tolerant_text_mention(bot: Bot, event: Event) -> None:
    """兼容文本形式的 "@昵称/@QQ号"：QQ 客户端未生成真实 @ 段时也按"提及"处理。

    运行在命令解析之前：所以 "@dd19 /ping" 这类"文字@ + 命令"也能命中命令。
    """
    if not isinstance(event, GroupMessageEvent) or event.is_tome():
        return
    message = event.get_message()
    if not message or message[0].type != "text":
        return
    text = str(message[0].data.get("text", ""))
    new_text, matched = strip_text_mention(text, await _bot_aliases(bot))
    if not matched:
        return
    message[0].data["text"] = new_text
    event.to_me = True


def _format_reply(reply: llm.Reply) -> str:
    cfg = get_config()
    # 先清洗模型原文（删含中文的括号旁白），再追加我们自己的提示——提示不经过清洗
    out = textnorm.clean_reply(reply.text) if cfg.reply_clean_enabled else reply.text
    if reply.truncated:
        out += "\n（回答超长被截断，可调大 LLM_MAX_TOKENS）"
    if cfg.llm_show_reasoning and reply.reasoning:
        excerpt = reply.reasoning[:120]
        out += f"\n\n🤔 思考节选：{excerpt}{'…' if len(reply.reasoning) > 120 else ''}"
    if cfg.llm_show_provider:
        out += f"\n[via {reply.provider}]"
    return out


def _make_sticker_handler(bot: Bot, event: GroupMessageEvent):
    """send_sticker 工具的执行器：挑图→发送→记账→返回结果文本。每轮回复最多发 1 张。"""
    sent = {"n": 0}

    async def _handler(name: str, args: dict) -> str:
        if name != "send_sticker":
            return f"未知工具 {name}"
        if sent["n"] >= 1:
            return "本轮已经发过表情包了，先别刷图，用文字接着说"
        query = str(args.get("query") or "").strip()
        repeat = bool(args.get("repeat"))
        row = stickers.pick(query, event.group_id, repeat=repeat)
        if row is None:
            return "图库还是空的（还没从群里收到可用图片），先用文字回复吧"
        try:
            uri = Path(str(row["path"])).resolve().as_uri()
            await bot.send(event, MessageSegment.image(uri))
        except Exception as exc:  # noqa: BLE001
            return f"表情包发送失败：{exc}"
        sent["n"] += 1
        stickers.note_sent(event.group_id, str(row["md5"]))
        caption = str(row["caption"] or "")[:60]
        try:
            context.record_message(
                event.group_id, int(event.self_id), personas.name_for(event.group_id), f"[表情包: {caption}]"
            )
        except Exception:  # noqa: BLE001 —— 记录失败不影响发送
            pass
        return f"已发送表情包（{caption}）"

    return _handler


async def _send_reply(send, event: GroupMessageEvent, text: str) -> None:
    """发送聊天回复：带引用段（引用触发消息；QUOTE_REPLY_ENABLED=0 可关）。"""
    cfg = get_config()
    msg: str | Message = text
    mid = getattr(event, "message_id", None)
    if cfg.quote_reply_enabled and mid:
        msg = MessageSegment.reply(int(mid)) + text
    await send(msg)


async def chat_flow(
    bot: Bot,
    event: GroupMessageEvent,
    text: str,
    send,
    *,
    quiet_skip: bool = False,
    extra_note: str = "",
    addressed: bool = True,
) -> None:
    """公共聊天核心：总开关 → 配额 → 限频 → 群上下文组装 → 主选+回退链 → 发送 → 记账。

    quiet_skip=True（决策层主动接话）：配额/限频/失败一律静默跳过，绝不刷提示；
    addressed=False：消息并非对机器人说（主动接话场景，prompt 措辞不同）。
    """
    cfg = get_config()
    if not cfg.llm_enabled:
        if not quiet_skip:
            await _send_reply(send, event, "聊天功能未启用（把 bot/.env 里 LLM_ENABLED 改为 1 并重启）")
        return
    if budget.exhausted():
        if not quiet_skip:
            await _send_reply(send, event, cfg.llm_quota_reply)
        return
    if not quiet_skip and not llm.allow(event.group_id, event.user_id):
        await _send_reply(send, event, "说得太快啦，稍等几秒再聊")
        return
    mem_block = ""
    if cfg.memory_enabled:
        try:
            mentioned: list[int] = []
            for seg in event.get_message():
                if seg.type == "at":
                    qq = str(seg.data.get("qq") or "")
                    if qq.isdigit() and qq != str(event.self_id) and int(qq) not in mentioned:
                        mentioned.append(int(qq))
            mem_block = memory.for_prompt(
                event.group_id, event.user_id, extra_user_ids=mentioned, query_text=text
            )
        except Exception:  # noqa: BLE001 —— 记忆异常不影响聊天
            mem_block = ""
    prompt = text
    if cfg.llm_context_messages > 0:
        # 群上下文：取触发消息之前最近几条（触发消息此刻已在库，需剔除）
        history = [
            row
            for row in context.recent_messages(event.group_id, limit=cfg.llm_context_messages + 1)
            if str(row.get("message_id")) != str(event.message_id)
        ][-cfg.llm_context_messages :]
        prompt = context.format_context_prompt(
            history, sender_name(event), text, addressed=addressed, memories=mem_block, self_qq=int(event.self_id)
        )
    elif mem_block:
        prompt = f"{mem_block}\n\n{text}"
    persona_name, persona_text = personas.resolve(event.group_id)
    extra_tools = None
    tool_handler = None
    blocks: list[str] = []
    if cfg.style_enabled:
        try:
            style_block = style_pairs.for_prompt(event.group_id, text, persona=persona_name)
            if style_block:
                blocks.append(style_block)
        except Exception:  # noqa: BLE001 —— 风格库异常不影响聊天
            pass
    if cfg.jargon_enabled:
        try:
            jargon_block = jargon.for_prompt(event.group_id, text)
            if jargon_block:
                blocks.append(jargon_block)
        except Exception:  # noqa: BLE001 —— 黑话异常不影响聊天
            pass
    if cfg.affection_enabled and addressed:
        try:
            # 先结算这次互动（回复前更新，回复就能带上最新关系）
            affection.update(event.group_id, event.user_id, sender_name(event), text)
            aff_block = affection.for_prompt(event.group_id, event.user_id, sender_name(event))
            if aff_block:
                blocks.append(aff_block)
        except Exception:  # noqa: BLE001 —— 好感度异常不影响聊天
            pass
    if cfg.mood_enabled:
        try:
            mood_block = mood.for_prompt(event.group_id)
            if mood_block:
                blocks.append(mood_block)
        except Exception:  # noqa: BLE001 —— 心情异常不影响聊天
            pass
    if cfg.sticker_enabled:
        try:
            if context.sticker_count() > 0:
                extra_tools = [stickers.tool_spec()]
                tool_handler = _make_sticker_handler(bot, event)
                hint = stickers.chat_hint()
                lib = stickers.library_summary(limit=12)
                if lib:
                    hint = f"{hint}\n图库现有（挑 query 时参考）：{lib}"
                blocks.append(hint)
        except Exception:  # noqa: BLE001 —— 贴图库异常不影响聊天
            extra_tools, tool_handler = None, None
    if extra_note:
        blocks.append(extra_note)
    dynamic_blocks = "\n\n".join(blocks)  # 动态块拼用户消息尾部（保 system 前缀缓存）
    try:
        reply = await llm.chat(
            prompt,
            session_key=f"qqbot-group-{event.group_id}",
            extra_tools=extra_tools,
            tool_handler=tool_handler,
            dynamic_blocks=dynamic_blocks,
            extra_system=_CHAT_RULES,
            system_prompt_override=persona_text,
        )
    except llm.QuotaExceededError:
        if not quiet_skip:
            await _send_reply(send, event, cfg.llm_quota_reply)
        return
    except Exception as exc:  # noqa: BLE001 —— 失败给用户一句人话，异常细节只进日志
        _log.opt(exception=True).warning("chat reply failed [group {}]: {}", event.group_id, exc)
        if not quiet_skip:
            await _send_reply(send, event, cfg.llm_error_reply)
        return
    out = _format_reply(reply)
    try:
        # 记录机器人自己的回复，保持群上下文完整（bot 也是"群友"；记录名=该群人设名）
        context.record_message(event.group_id, int(event.self_id), persona_name, out)
    except Exception:  # noqa: BLE001 —— 记录失败不影响回复
        pass
    await _send_reply(send, event, out)


async def _reply_chat(bot: Bot, matcher, event: GroupMessageEvent, text: str) -> None:
    """@/命令触发的聊天入口：走公共核心，提示照常可见。"""
    await chat_flow(bot, event, text, matcher.finish)


@chat_all.handle()
async def _handle_plain(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = get_config()
    message = event.get_message()
    # 先做廉价判定（不触发 API）：昵称解析只在"确认要回复"后进行
    probe = render_message_text(message, str(bot.self_id))
    if not probe:
        # 只 @ 了机器人却没写内容：给个引导提示（command 模式除外）
        if event.is_tome() and cfg.llm_reply_mode != "command":
            await chat_all.finish("在的～在 @我 后面写上想聊的内容就行")
        return
    if not should_reply_plain(probe, event.is_tome(), cfg.llm_reply_mode):
        return
    names = await resolve_at_names(bot, event.group_id, message)
    text = render_message_text(message, str(bot.self_id), names)
    await _reply_chat(bot, chat_all, event, text)


@chat.handle()
async def _handle_chat(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    probe = render_message_text(args, str(bot.self_id))
    if not probe:
        await chat.finish("用法：/chat 你想说的话")
    names = await resolve_at_names(bot, event.group_id, args)
    text = render_message_text(args, str(bot.self_id), names)
    await _reply_chat(bot, chat, event, text)


def _build_search_prompt(query: str, results: list[search.SearchResult]) -> str:
    lines = [f"- {r.title}：{r.snippet[:200]}（{r.url}）" for r in results]
    return (
        f"帮我根据网络搜索结果回答问题。搜索词：{query}\n"
        "搜索结果：\n" + "\n".join(lines) + "\n"
        "请用简短口语（1~3 句）总结最相关、可信的信息；不要编造；信息不足就直说。"
    )


def _format_search_links(results: list[search.SearchResult], top: int = 3) -> str:
    lines = [f"{i}. {r.title or r.url} {r.url}" for i, r in enumerate(results[:top], 1)]
    return "参考链接：\n" + "\n".join(lines)


async def _do_search(matcher, event: GroupMessageEvent, query: str) -> None:
    """搜索流程：配额/限频 → Firecrawl 搜索 → LLM 总结（失败退化为纯链接）。"""
    cfg = get_config()
    if not cfg.search_enabled:
        await matcher.finish("搜索功能未启用（SEARCH_ENABLED=1 开启）")
    if budget.exhausted():
        await matcher.finish(cfg.llm_quota_reply)
    if not llm.allow(event.group_id, event.user_id):
        await matcher.finish("说得太快啦，稍等几秒再聊")
    try:
        results = await search.web_search(
            query,
            api_key=cfg.search_api_key,
            base_url=cfg.search_base_url,
            limit=cfg.search_max_results,
            timeout=cfg.search_timeout,
        )
    except Exception as exc:  # noqa: BLE001 —— 细节只进日志，给用户一句人话
        _log.opt(exception=True).warning("search failed: {}", exc)
        await matcher.finish("搜索失败了喵…网络好像不太顺，等会儿再试？")
        return
    if not results:
        await matcher.finish("没有搜到相关内容喵…")
    summary = ""
    try:
        reply = await llm.chat(
            _build_search_prompt(query, results), session_key=f"qqbot-group-{event.group_id}"
        )
        summary = reply.text
    except llm.QuotaExceededError:
        await matcher.finish(cfg.llm_quota_reply)
        return
    except Exception:  # noqa: BLE001 —— 总结失败就只发链接
        summary = ""
    out = (summary.strip() + "\n\n" if summary.strip() else "") + _format_search_links(results)
    await matcher.finish(out)


@search_cmd.handle()
async def _handle_search(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    query = args.extract_plain_text().strip()
    if not query:
        await search_cmd.finish("用法：/search 关键词（或 @我 发「搜索 关键词」）")
    await _do_search(search_cmd, event, query)


@usage_cmd.handle()
async def _handle_usage() -> None:
    cfg = get_config()
    st = budget.status()
    lines = [f"今日已用 {st['total']:,} / {cfg.llm_daily_token_limit:,} tokens（{st['date']}）"]
    for name, used in sorted(st.get("by_provider", {}).items()):
        lines.append(f"- {name}: {used:,}")
    lines.append(f"剩余：{budget.remaining():,}")
    await usage_cmd.finish("\n".join(lines))


def _provider_line(name: str) -> str:
    cfg = get_config()
    provider = cfg.providers[name]
    key_state = "无需key" if name == "local" else ("有key" if provider.api_key else "缺key!")
    mark = " ←当前" if name == llm.current_default() else ""
    return f"- {name}: {provider.model}{mark} [{key_state}]"


@model_cmd.handle()
async def _handle_model(args: Message = CommandArg()) -> None:
    cfg = get_config()
    parts = args.extract_plain_text().strip().split()
    if not parts:
        lines = [f"当前模型：{llm.current_default()}", "可用后端："]
        lines += [_provider_line(name) for name in cfg.providers]
        lines.append("用法：/model <名称> 切换；/model test <名称> 连通性测试")
        await model_cmd.finish("\n".join(lines))

    if parts[0] == "test":
        raw = parts[1] if len(parts) > 1 else llm.current_default()
        name = normalize_provider_name(raw) or raw
        if name not in cfg.providers:
            await model_cmd.finish(f"未知后端：{raw}")
        if budget.exhausted():
            await model_cmd.finish(cfg.llm_quota_reply + "（今日额度已用完）")
        start = time.monotonic()
        try:
            completion = await llm.chat_once(
                "你好，请只回复两个字：正常", cfg.providers[name], session_key="admin-test"
            )
            cost = time.monotonic() - start
            think = f"，思考 {len(completion.reasoning)} 字" if completion.reasoning else "，未见思考输出"
            await model_cmd.finish(f"{name}: OK（{cost:.1f}s{think}）→ {completion.text[:30]}")
        except llm.QuotaExceededError:
            await model_cmd.finish(cfg.llm_quota_reply + "（今日额度已用完）")
        except Exception as exc:  # noqa: BLE001
            await model_cmd.finish(f"{name}: 失败：{type(exc).__name__}（{exc}）")

    raw = parts[0]
    name = normalize_provider_name(raw) or raw
    if name not in cfg.providers:
        await model_cmd.finish(f"未知后端：{raw}（可用：{', '.join(cfg.providers)}）")
    llm.set_default(name)
    provider = cfg.providers[name]
    note = "" if (name == "local" or provider.api_key) else "（注意：该后端还没配 API key）"
    await model_cmd.finish(f"已切换默认模型为 {name}{note}")


@persona_cmd.handle()
async def _handle_persona(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    """按群人设切换 + 学习补充审查（仅管理员；立即生效，无需重启）。"""
    gid = int(event.group_id)
    parts = args.extract_plain_text().strip().split()
    if parts and parts[0].lower() in ("review", "approve", "reject", "evolve", "patches", "patch"):
        await persona_cmd.finish(await persona_evo.handle_command(gid, parts, get_config()))
        return
    if parts and parts[0].lower() == "default":
        personas.clear_group(gid)
        name, _ = personas.resolve(gid)
        await persona_cmd.finish(f"已恢复默认人设：{name}（立即生效）")
        return
    if parts:
        item = personas.match(parts[0])
        if not item:
            names = " / ".join(p["name"] for p in personas.list_personas())
            await persona_cmd.finish(f"没找到这个人设。可用：{names}")
            return
        personas.set_group(gid, item["stem"])
        await persona_cmd.finish(f"本群人设已切换为 {item['name']}（立即生效，下一条回复就是新人格）")
        return
    name, _ = personas.resolve(gid)
    stem = personas.get_group_stem(gid)
    names = " / ".join(p["name"] for p in personas.list_personas())
    pending = persona_evo.pending_count(gid)
    extra = f"\n待审学习建议 {pending} 条（/persona review）" if pending else ""
    await persona_cmd.finish(
        f"当前人设：{name}" + ("（本群自定义）" if stem else "（默认）")
        + f"\n可用：{names}{extra}\n"
        "用法：/persona <名字> 切换；/persona default 恢复默认；/persona evolve 生成学习建议；/persona review 查看待审"
    )
