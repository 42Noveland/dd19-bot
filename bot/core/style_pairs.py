"""风格学习（few-shot 对话对，借鉴 astrbot self-learning 的机械提取）：

从真实「用户→Bot」连续对话里提取风格样本（**零 LLM 成本**），回复时注入相似场合的样例供模仿。

- 提取（extract_group）：增量（style_watermark 水位）；只认真实 bot QQ（cfg.bot_qq）的回复行；
  跳过命令/媒体占位/过短文本；贴图行（[表情包: …]）不打断配对（图+文两条回复仍配到同一条用户消息）。
- 去重：同群同人设同 (situation, expression) 只存一条，复发刷新 last_active_ts（权重小幅上浮）。
- 注入（for_prompt）：bigram 与当前消息的相似度优先，其次新鲜度；只注入相似度 >0 的，最多 k 条。
- 清理（prune）：超过 KEEP_DAYS 未复发的删除；每群上限 MAX_PAIRS。
"""
from __future__ import annotations

import re
import time

from . import context
from .config import Config
from .decay import freshness

KEEP_DAYS = 30.0  # 未复发超过这么久就清理
MAX_PAIRS = 300  # 每群上限
_SIT_MAX = 40  # situation 截断
_EXP_MAX = 90  # expression 截断
_MIN_LEN = 3  # 过短的不学


def _bigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        for i in range(len(run) - 1):
            grams.add(run[i : i + 2])
    for word in re.findall(r"[A-Za-z0-9]+", text or ""):
        if len(word) >= 2:
            grams.add(word.lower())
    return grams


def _watermark(conn, group_id: int) -> int:
    row = conn.execute("SELECT last_row_id FROM style_watermark WHERE group_id=?", (int(group_id),)).fetchone()
    return int(row["last_row_id"]) if row else 0


def _set_watermark(group_id: int, last_row_id: int) -> None:
    with context._lock:
        conn = context._db()
        conn.execute(
            "INSERT INTO style_watermark(group_id, last_row_id, updated_ts) VALUES(?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET last_row_id=excluded.last_row_id, updated_ts=excluded.updated_ts",
            (int(group_id), int(last_row_id), time.time()),
        )
        conn.commit()


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _ok_situation(text: str) -> bool:
    t = _clean(text)
    if len(t) < _MIN_LEN or t.startswith(("[", "http")):
        return False
    if t[:1] in ("/", "!", "#", "."):
        return False
    return True


def _ok_expression(text: str) -> bool:
    t = _clean(text)
    return len(t) >= _MIN_LEN and not t.startswith(("[", "http"))


def _upsert_pair(group_id: int, persona: str, situation: str, expression: str, now: float) -> bool:
    sit = _clean(situation)[:_SIT_MAX]
    exp = _clean(expression)[:_EXP_MAX]
    if not sit or not exp:
        return False
    with context._lock:
        conn = context._db()
        conn.execute(
            "INSERT INTO style_pairs(group_id, persona, situation, expression, weight, last_active_ts, create_ts) "
            "VALUES(?,?,?,?,1.0,?,?) "
            "ON CONFLICT(group_id, persona, situation, expression) DO UPDATE SET "
            "last_active_ts=excluded.last_active_ts, weight=MIN(weight+0.2, 1.5)",
            (int(group_id), str(persona), sit, exp, now, now),
        )
        conn.commit()
    return True


def extract_group(group_id: int, cfg: Config) -> tuple[int, int]:
    """增量提取一批（处理水位之后的 bot 回复行）；返回 (新处理行数, 新增/刷新对数)。"""
    gid = int(group_id)
    bot_qq = int(cfg.bot_qq)
    prune(gid)
    with context._lock:
        conn = context._db()
        wm = _watermark(conn, gid)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM messages WHERE group_id=? AND id>? ORDER BY id", (gid, wm)
        ).fetchall()]
        if not rows:
            return 0, 0
        prev = conn.execute(
            "SELECT * FROM messages WHERE group_id=? AND id<=? ORDER BY id DESC LIMIT 1", (gid, wm)
        ).fetchone()
    seq = ([dict(prev)] if prev else []) + rows
    added = 0
    last_user: dict | None = None
    now = time.time()
    for row in seq:
        uid = int(row.get("user_id") or 0)
        text = str(row.get("text") or "")
        is_new_bot = uid == bot_qq and int(row.get("id") or 0) > wm
        if uid == bot_qq:
            if text.startswith("["):
                continue  # 贴图等媒体占位行：不打断配对
            if last_user is not None and is_new_bot and _ok_expression(text) and _ok_situation(last_user["text"]):
                if _upsert_pair(gid, str(row.get("name") or ""), last_user["text"], text, now):
                    added += 1
            if is_new_bot:
                last_user = None
        else:
            last_user = row
    _set_watermark(gid, int(rows[-1]["id"]))
    return len(rows), added


def prune(group_id: int) -> None:
    """清理过期/超量的风格对。"""
    gid = int(group_id)
    cutoff = time.time() - KEEP_DAYS * 86400
    with context._lock:
        conn = context._db()
        conn.execute("DELETE FROM style_pairs WHERE group_id=? AND last_active_ts < ?", (gid, cutoff))
        conn.execute(
            "DELETE FROM style_pairs WHERE group_id=? AND id NOT IN ("
            "SELECT id FROM style_pairs WHERE group_id=? ORDER BY last_active_ts DESC LIMIT ?)",
            (gid, gid, MAX_PAIRS),
        )
        conn.commit()


def list_pairs(group_id: int, persona: str | None = None, limit: int = 50) -> list[dict]:
    """列出风格对（测试/命令用）。"""
    gid = int(group_id)
    with context._lock:
        conn = context._db()
        if persona:
            rows = conn.execute(
                "SELECT * FROM style_pairs WHERE group_id=? AND persona=? ORDER BY last_active_ts DESC LIMIT ?",
                (gid, str(persona), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM style_pairs WHERE group_id=? ORDER BY last_active_ts DESC LIMIT ?",
                (gid, int(limit)),
            ).fetchall()
    return [dict(r) for r in rows]


def for_prompt(group_id: int, query_text: str = "", persona: str = "", k: int = 3) -> str:
    """挑相似场合的风格样例拼成提示块；没有相似的就返回空串。"""
    gid = int(group_id)
    with context._lock:
        conn = context._db()
        if persona:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM style_pairs WHERE group_id=? AND persona=? ORDER BY last_active_ts DESC LIMIT 200",
                (gid, str(persona)),
            ).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM style_pairs WHERE group_id=? ORDER BY last_active_ts DESC LIMIT 200",
                (gid,),
            ).fetchall()]
    if not rows:
        return ""
    q = _bigrams(query_text)
    scored: list[tuple[int, float, dict]] = []
    for r in rows:
        kw = len(q & _bigrams(r.get("situation") or "")) if q else 0
        scored.append((kw, freshness(r.get("last_active_ts")), r))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    picked = [t for t in scored if t[0] > 0][: max(1, int(k))]
    if not picked:
        return ""
    lines = ["【风格参考】你以前在类似场合的回复（模仿这个语气，不要照抄原话）："]
    for _, _, r in picked:
        lines.append(f"- 对方说「{r['situation']}」→ 你回「{r['expression']}」")
    return "\n".join(lines)


def stats_for(group_id: int) -> dict:
    gid = int(group_id)
    with context._lock:
        conn = context._db()
        n = conn.execute("SELECT COUNT(*) AS n FROM style_pairs WHERE group_id=?", (gid,)).fetchone()["n"]
        wm = _watermark(conn, gid)
    return {"pairs": int(n or 0), "watermark": wm}
