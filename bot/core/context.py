"""群上下文存储（感知层地基）：SQLite 记录白名单群的全部消息 + 图片描述缓存（兼贴图库底账）。

- messages：每条群消息一行（含 @ 渲染后的文本、图片占位）；滚动保留（7 天 / 每群 5000 条）。
- images：  图片 md5 → 本地文件 + VLM 描述（caption 缓存；也是"斗图收集器"的底账）。

对齐 core/budget.py 的工程风格：模块级连接 + 线程锁；测试用 reset(path) 切库。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "context.db"
KEEP_DAYS = 7
MAX_PER_GROUP = 5000

_conn: sqlite3.Connection | None = None
_path: Path = _DEFAULT_PATH
_lock = threading.Lock()
_writes_since_prune = 0


def reset(path: str | Path | None = None) -> None:
    """测试用：关闭当前连接并切换 db 路径（None = 恢复默认）。"""
    global _conn, _path, _writes_since_prune
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _conn = None
        _path = Path(path) if path else _DEFAULT_PATH
        _writes_since_prune = 0


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                message_id INTEGER,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                text TEXT NOT NULL,
                media TEXT NOT NULL DEFAULT '',
                ts REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts);
            CREATE INDEX IF NOT EXISTS idx_messages_gid_mid ON messages(group_id, message_id);
            CREATE TABLE IF NOT EXISTS images (
                md5 TEXT PRIMARY KEY,
                path TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                seen_count INTEGER NOT NULL DEFAULT 1,
                first_ts REAL NOT NULL,
                last_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'user',
                text TEXT NOT NULL,
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memories_group_user ON memories(group_id, user_id);
            CREATE TABLE IF NOT EXISTS mem_watermark (
                group_id INTEGER PRIMARY KEY,
                last_row_id INTEGER NOT NULL DEFAULT 0,
                updated_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mood (
                group_id INTEGER PRIMARY KEY,
                mood TEXT NOT NULL,
                intensity REAL NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                updated_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS personas (
                group_id INTEGER PRIMARY KEY,
                persona TEXT NOT NULL,
                updated_ts REAL NOT NULL
            );
            """
        )
        conn.commit()
        _prune_locked(conn)
        _conn = conn
    return _conn


def _prune_locked(conn: sqlite3.Connection) -> None:
    """保留 7 天 + 每群最多 MAX_PER_GROUP 条。"""
    cutoff = time.time() - KEEP_DAYS * 86400
    conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
    conn.execute(
        """
        DELETE FROM messages WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY group_id ORDER BY id DESC) AS rn
                FROM messages
            ) WHERE rn > ?
        )
        """,
        (MAX_PER_GROUP,),
    )
    conn.commit()


# ---------------- messages ----------------

def record_message(
    group_id: int,
    user_id: int,
    name: str,
    text: str,
    *,
    message_id: int | None = None,
    media: list[dict] | None = None,
    ts: float | None = None,
) -> int:
    global _writes_since_prune
    now = time.time() if ts is None else ts
    media_json = json.dumps(media, ensure_ascii=False) if media else ""
    with _lock:
        conn = _db()
        cur = conn.execute(
            "INSERT INTO messages(group_id, message_id, user_id, name, text, media, ts) VALUES(?,?,?,?,?,?,?)",
            (
                int(group_id),
                int(message_id) if message_id else None,
                int(user_id),
                str(name),
                str(text),
                media_json,
                now,
            ),
        )
        conn.commit()
        row_id = int(cur.lastrowid or 0)
        _writes_since_prune += 1
        if _writes_since_prune >= 200:
            _writes_since_prune = 0
            _prune_locked(conn)
    return row_id


def recent_messages(group_id: int, limit: int = 12) -> list[dict]:
    """最近 limit 条消息（按时间正序返回）。"""
    if limit <= 0:
        return []
    with _lock:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
            (int(group_id), int(limit)),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_message(row_id: int) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM messages WHERE id=?", (int(row_id),)).fetchone()
    return dict(row) if row else None


def update_message_text(row_id: int, text: str) -> None:
    with _lock:
        conn = _db()
        conn.execute("UPDATE messages SET text=? WHERE id=?", (str(text), int(row_id)))
        conn.commit()


def message_exists(group_id: int, message_id: int | None) -> bool:
    if not message_id:
        return False
    with _lock:
        row = _db().execute(
            "SELECT 1 FROM messages WHERE group_id=? AND message_id=? LIMIT 1",
            (int(group_id), int(message_id)),
        ).fetchone()
    return row is not None


# ---------------- images（caption 缓存 / 贴图库底账） ----------------

def image_get(md5: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM images WHERE md5=?", (md5,)).fetchone()
    return dict(row) if row else None


def image_touch(md5: str) -> None:
    with _lock:
        conn = _db()
        conn.execute(
            "UPDATE images SET seen_count=seen_count+1, last_ts=? WHERE md5=?",
            (time.time(), md5),
        )
        conn.commit()


def image_upsert(md5: str, path: str, caption: str, model: str) -> None:
    now = time.time()
    with _lock:
        conn = _db()
        row = conn.execute("SELECT * FROM images WHERE md5=?", (md5,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO images(md5, path, caption, model, seen_count, first_ts, last_ts) VALUES(?,?,?,?,1,?,?)",
                (md5, path, caption, model, now, now),
            )
        else:
            new_caption = caption or row["caption"]
            new_model = model if caption else row["model"]
            conn.execute(
                "UPDATE images SET path=COALESCE(NULLIF(?,''), path), caption=?, model=?, "
                "seen_count=seen_count+1, last_ts=? WHERE md5=?",
                (path, new_caption, new_model, now, md5),
            )
        conn.commit()


# ---------------- 贴图库（表情包候选） ----------------

def sticker_count() -> int:
    """图库里可发送的图片数（有描述且路径非空）。"""
    with _lock:
        row = _db().execute(
            "SELECT COUNT(*) AS n FROM images WHERE caption != '' AND path != ''"
        ).fetchone()
    return int(row["n"] or 0)


def sticker_candidates(limit: int = 500) -> list[dict]:
    """贴图库候选：有描述的图片（按人气/新鲜度排序）。"""
    with _lock:
        rows = _db().execute(
            "SELECT * FROM images WHERE caption != '' AND path != '' "
            "ORDER BY seen_count DESC, last_ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- prompt 组装 ----------------

def format_context_prompt(
    history: list[dict],
    speaker: str,
    text: str,
    addressed: bool = True,
    memories: str = "",
) -> str:
    """把群聊历史 + 当前消息组装成给 LLM 的最终 prompt（纯函数，可单测）。

    addressed=False：消息并非直接对机器人说（决策层主动接话场景），措辞不同。
    memories：记忆系统注入块（可空），插在群聊背景之后。
    """
    tail = f"{speaker} 对你说：{text}" if addressed else f"群里 {speaker} 说：{text}"
    if not history:
        return f"{memories}\n\n{tail}" if memories else tail
    lines: list[str] = []
    for row in history:
        name = str(row.get("name") or row.get("user_id") or "?")
        content = str(row.get("text") or "").strip()
        if content:
            lines.append(f"{name}: {content}")
    if not lines:
        return f"{memories}\n\n{tail}" if memories else tail
    now = f"【现在，{speaker} 对你说】" if addressed else f"【现在，群里 {speaker} 说（没有人 @ 你）】"
    mid = f"\n\n{memories}" if memories else ""
    return (
        "【群聊背景（最近几条消息，供你了解上下文，不用逐条回应）】\n"
        + "\n".join(lines)
        + mid
        + f"\n\n{now}\n{text}"
    )
