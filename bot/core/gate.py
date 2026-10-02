"""群白名单判定 + 默认聊天模式的消息路由判定 + 文本形式提及兼容。"""
from __future__ import annotations

from .config import Config, get_config


def is_allowed_group(group_id: int | None, config: Config | None = None) -> bool:
    if group_id is None:
        return False
    cfg = config or get_config()
    return group_id in cfg.allowed_group_ids


def should_reply_plain(text: str, mentioned: bool, mode: str) -> bool:
    """默认模式的消息路由：
    - all：所有非空、非斜杠消息都交给 AI（无前缀直接聊）
    - mention：仅当被 @（mentioned=True）
    - command：不主动回（仅 /chat）
    斜杠开头的消息一律跳过（命令或疑似命令，保持安静）。"""
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return False
    if mode == "command":
        return False
    if mode == "mention":
        return mentioned
    return True


def strip_text_mention(text: str, aliases: set[str]) -> tuple[str, bool]:
    """把文本形式的 "@昵称/@QQ号" 前缀当作提及处理（兼容 QQ 客户端未生成真实 @ 段的情况）。

    仅当 "@" 后紧跟机器人别名（登录昵称或 QQ 号）时命中；别名后若是 ASCII 字母/数字，
    则要求有分隔符，避免 "@dd190" 这类误触发。返回 (去掉前缀后的文本, 是否命中)。
    """
    stripped = text.lstrip()
    if not stripped.startswith("@"):
        return text, False
    body = stripped[1:]
    for alias in aliases:
        if not alias or not body.startswith(alias):
            continue
        rest = body[len(alias):]
        if not rest:
            return "", True
        if rest[0].isspace() or rest[0] in _MENTION_SEPS:
            return rest.lstrip(_MENTION_SEPS).strip(), True
        if not (rest[0].isascii() and rest[0].isalnum()):
            return rest.strip(), True
    return text, False


_MENTION_SEPS = " \t\u3000，,：:、。!！?？~～"
