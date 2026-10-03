"""按群人设系统：每个群可独立选择人设文件（bot/persona*.md），超级用户可动态切换。

- 存储：context.db 的 personas 表（group_id → 人设文件 stem；无记录 = 用默认人设）。
- 默认：.env 的 LLM_PERSONA_FILE（或 LLM_SYSTEM_PROMPT 直写）。
- 缓存：按文件 mtime 缓存内容——改 md 文件即热更新，无需重启。
- 切换：/persona 命令（plugins/llm_chat.py）立即生效（每次回复实时解析）。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import context
from .config import get_config

_BASE = Path(__file__).resolve().parents[1]  # bot/
_cache: dict[str, tuple[float, str, str]] = {}  # path -> (mtime, text, name)


def _load(path: Path) -> tuple[str, str]:
    """读人设文件（按 mtime 缓存）：返回 (文本, 名字)。"""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except Exception:  # noqa: BLE001
        return "", ""
    cached = _cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        text = ""
    name = _extract_name(text) or path.stem
    _cache[key] = (mtime, text, name)
    return text, name


def _extract_name(text: str) -> str:
    m = re.search(r"^#\s*人设[:：]\s*([^\s（(]+)", text or "", flags=re.M)
    return m.group(1).strip() if m else ""


def list_personas() -> list[dict]:
    """可用人设清单：[{stem, name, path}]（按文件名排序）。"""
    out: list[dict] = []
    for p in sorted(_BASE.glob("persona*.md")):
        text, name = _load(p)
        if text:
            out.append({"stem": p.stem, "name": name, "path": str(p)})
    return out


def match(query: str) -> dict | None:
    """按名字或文件名匹配人设（大小写不敏感；'sparkle'/'persona-sparkle' 均可）。"""
    q = (query or "").strip().lower()
    if not q:
        return None
    for item in list_personas():
        stem = item["stem"].lower()
        if q in (stem, item["name"].lower()):
            return item
        if q == stem.removeprefix("persona-").removeprefix("persona"):
            return item
    return None


def get_group_stem(group_id: int) -> str:
    with context._lock:
        row = context._db().execute(
            "SELECT persona FROM personas WHERE group_id=?", (int(group_id),)
        ).fetchone()
    return str(row["persona"]) if row else ""


def set_group(group_id: int, stem: str) -> None:
    with context._lock:
        conn = context._db()
        conn.execute(
            "INSERT INTO personas(group_id, persona, updated_ts) VALUES(?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET persona=excluded.persona, updated_ts=excluded.updated_ts",
            (int(group_id), str(stem), time.time()),
        )
        conn.commit()


def clear_group(group_id: int) -> None:
    with context._lock:
        conn = context._db()
        conn.execute("DELETE FROM personas WHERE group_id=?", (int(group_id),))
        conn.commit()


def resolve(group_id: int) -> tuple[str, str]:
    """解析该群的人设：(名字, 文本)。本群有自定义且文件可读 → 用它；否则用默认。
    已批准的本群学习补充会拼在文本尾部（不改 md 原文，/persona patch rm 可回滚）。"""
    stem = get_group_stem(group_id)
    if stem:
        text, name = _load(_BASE / f"{stem}.md")
        if text:
            name = name or stem
            return name, _with_patches(group_id, name, text)
    cfg = get_config()
    name = _extract_name(cfg.llm_system_prompt) or cfg.bot_name
    return name, _with_patches(group_id, name, cfg.llm_system_prompt)


def _with_patches(group_id: int, name: str, text: str) -> str:
    """把已批准的本群学习补充拼在人设尾部（persona_patches 表；/persona patch rm 可回滚）。"""
    try:
        from . import persona_evo  # 延迟导入避免环

        patches = persona_evo.patches_for(group_id, name)
    except Exception:  # noqa: BLE001 —— 补充读取失败不影响人设
        return text
    if not patches:
        return text
    lines = "\n".join(f"- {t}" for _, t in patches)
    return f"{text}\n\n【本群学习补充（管理员已批准）】\n{lines}"


def name_for(group_id: int) -> str:
    """该群机器人消息的记录名（= 该群人设名）。"""
    return resolve(group_id)[0]
