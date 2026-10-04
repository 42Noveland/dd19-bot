"""探针公共环境：真实号码从 bot/.env 读取，仓库里不存任何真实群号/QQ。

优先级：环境变量 PROBE_GROUP / PROBE_ADMIN / PROBE_BOT_QQ > bot/.env。
拿不到时抛出带指引的 RuntimeError（先配好 bot/.env 再跑探针）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


_ENV = _dotenv()


def _as_int(raw: str, what: str, hint: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"无法解析{what}：{raw!r}——{hint}") from None


def group() -> int:
    """探针用群号：必须是 bot 白名单里的群（默认取 ALLOWED_GROUP_IDS 第一个）。"""
    raw = (os.environ.get("PROBE_GROUP") or _ENV.get("ALLOWED_GROUP_IDS", "").split(",")[0]).strip()
    if not raw:
        raise RuntimeError("无法确定探针群号：请在 bot/.env 配置 ALLOWED_GROUP_IDS，或设环境变量 PROBE_GROUP")
    return _as_int(raw, "探针群号", "请在 bot/.env 的 ALLOWED_GROUP_IDS 里填真实群号")


def admin() -> int:
    """探针用管理员 QQ：必须是 SUPERUSERS 之一（默认取第一个）。"""
    raw = os.environ.get("PROBE_ADMIN", "").strip()
    if not raw:
        try:
            users = json.loads(_ENV.get("SUPERUSERS", "[]"))
            raw = str(users[0]).strip() if users else ""
        except (ValueError, TypeError, IndexError):
            raw = ""
    if not raw:
        raise RuntimeError("无法确定管理员 QQ：请在 bot/.env 配置 SUPERUSERS，或设环境变量 PROBE_ADMIN")
    return _as_int(raw, "管理员 QQ", "请在 bot/.env 的 SUPERUSERS 里填真实管理员号")


def bot_qq() -> int:
    """机器人 QQ：默认取 bot/.env 的 BOT_QQ。"""
    raw = (os.environ.get("PROBE_BOT_QQ") or _ENV.get("BOT_QQ", "")).strip()
    if not raw:
        raise RuntimeError("无法确定机器人 QQ：请在 bot/.env 配置 BOT_QQ，或设环境变量 PROBE_BOT_QQ")
    return _as_int(raw, "机器人 QQ", "请在 bot/.env 的 BOT_QQ 里填真实号")
