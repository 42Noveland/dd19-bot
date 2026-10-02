"""/chat 指令 + 默认聊天（@我即聊，LLM_REPLY_MODE 可调）+ /model 管理（仅管理员）。

默认聊天（LLM_REPLY_MODE=mention，默认值）：在群里 @机器人（或引用回复机器人消息）
即可直接对话，无需 /chat 前缀；LLM_REPLY_MODE=all 时所有非命令消息都会交给 AI；
LLM_REPLY_MODE=command 时仅 /chat 指令触发。斜杠开头的消息一律不按聊天处理。
"""
import time

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message
from nonebot.message import event_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from core import llm
from core.config import get_config, normalize_provider_name
from core.gate import is_allowed_group, should_reply_plain, strip_text_mention


async def _allowed(event: Event) -> bool:
    return is_allowed_group(getattr(event, "group_id", None))


# 默认聊天：适配器会把开头/结尾的 @机器人 剥离并置 event.to_me（引用回复机器人同样算）
chat_all = on_message(rule=Rule(_allowed), priority=50, block=False)
chat = on_command("chat", rule=Rule(_allowed), priority=20, block=True)
model_cmd = on_command("model", rule=Rule(_allowed), permission=SUPERUSER, priority=5, block=True)

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
    out = reply.text
    if reply.truncated:
        out += "\n（回答超长被截断，可调大 LLM_MAX_TOKENS）"
    if cfg.llm_show_reasoning and reply.reasoning:
        excerpt = reply.reasoning[:120]
        out += f"\n\n🤔 思考节选：{excerpt}{'…' if len(reply.reasoning) > 120 else ''}"
    if cfg.llm_show_provider:
        out += f"\n[via {reply.provider}]"
    return out


async def _reply_chat(matcher, event: GroupMessageEvent, text: str) -> None:
    """公共聊天流程：总开关 → 限频 → 主选+回退链 → 组装回复。"""
    cfg = get_config()
    if not cfg.llm_enabled:
        await matcher.finish("聊天功能未启用（把 bot/.env 里 LLM_ENABLED 改为 1 并重启）")
    if not llm.allow(event.group_id, event.user_id):
        await matcher.finish("说得太快啦，稍等几秒再聊")
    try:
        reply = await llm.chat(text, session_key=f"qqbot-group-{event.group_id}")
    except Exception as exc:  # noqa: BLE001 —— 所有后端都失败时给用户明确提示
        await matcher.finish(f"AI 调用失败（所有后端）：{exc}")
        return
    await matcher.finish(_format_reply(reply))


@chat_all.handle()
async def _handle_plain(event: GroupMessageEvent) -> None:
    cfg = get_config()
    text = event.get_message().extract_plain_text().strip()
    if not text:
        # 只 @ 了机器人却没写内容：给个引导提示（command 模式除外）
        if event.is_tome() and cfg.llm_reply_mode != "command":
            await chat_all.finish("在的～在 @我 后面写上想聊的内容就行")
        return
    if not should_reply_plain(text, event.is_tome(), cfg.llm_reply_mode):
        return
    await _reply_chat(chat_all, event, text)


@chat.handle()
async def _handle_chat(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    if not text:
        await chat.finish("用法：/chat 你想说的话")
    await _reply_chat(chat, event, text)


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
        start = time.monotonic()
        try:
            completion = await llm.chat_once(
                "你好，请只回复两个字：正常", cfg.providers[name], session_key="admin-test"
            )
            cost = time.monotonic() - start
            think = f"，思考 {len(completion.reasoning)} 字" if completion.reasoning else "，未见思考输出"
            await model_cmd.finish(f"{name}: OK（{cost:.1f}s{think}）→ {completion.text[:30]}")
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
