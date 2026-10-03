"""情绪状态系统：机器人有一个随互动缓慢变化的"心情"，回复时自然带上。

- 存储：每群一条（context.db 的 mood 表）。
- 衰减：强度按半衰期 HALF_LIFE 指数衰减，低于 MIN_INTENSITY 视为回到平静。
- 更新：plugins/mood_loop.py 后台循环调用 update_group（综合最近互动，LLM 产出心情）。
- 注入：for_prompt() 生成"【你现在的状态】"块，chat_flow 放进系统提示。
"""
from __future__ import annotations

import json
import math
import time

from . import context, llm
from .config import Config

MOODS = ("平静", "开心", "得意", "无语", "委屈", "恼火", "疲惫", "好奇")
HALF_LIFE = 2400.0   # 强度半衰期（秒）
MIN_INTENSITY = 0.2  # 低于此值视为回到平静
_MIN_MESSAGES = 6    # 攒够这么多条新消息才值得整理一次心情

_RULES = (
    "【任务】你是群聊成员「十九」。根据下面最近群里的互动，更新你此刻的心情。\n"
    "从这些心情里选一个：平静 / 开心 / 得意 / 无语 / 委屈 / 恼火 / 疲惫 / 好奇。\n"
    "考虑：群友怎么对你（夸你/怼你/逗你/冷落你）、聊了什么话题、你参与得怎么样。"
    "心情可以被新互动改变，也可以延续；没什么特别的事就慢慢回到平静。\n"
    '输出一行 JSON：{"mood":"…","intensity":0.0~1.0,"reason":"不超过20字的缘由"}，不要输出别的。'
)


def decay(intensity: float, age_seconds: float) -> float:
    """强度随时间的指数衰减（半衰期 HALF_LIFE 秒）。"""
    if age_seconds <= 0:
        return float(intensity)
    return float(intensity) * math.exp(-age_seconds * math.log(2) / HALF_LIFE)


def parse_mood_update(raw: str) -> dict | None:
    """解析心情更新输出（单行 JSON，容忍代码块包裹）；非法返回 None。"""
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("mood") or "").strip()
        if name not in MOODS:
            continue
        try:
            intensity = float(obj.get("intensity"))
        except Exception:  # noqa: BLE001
            intensity = 0.5
        intensity = max(0.0, min(1.0, intensity))
        reason = str(obj.get("reason") or "").strip()[:40]
        return {"mood": name, "intensity": intensity, "reason": reason}
    return None


def set_mood(group_id: int, mood: str, intensity: float, reason: str = "") -> None:
    with context._lock:
        conn = context._db()
        conn.execute(
            "INSERT INTO mood(group_id, mood, intensity, reason, updated_ts) VALUES(?,?,?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET mood=excluded.mood, intensity=excluded.intensity, "
            "reason=excluded.reason, updated_ts=excluded.updated_ts",
            (int(group_id), str(mood), float(intensity), str(reason), time.time()),
        )
        conn.commit()


def get_mood(group_id: int, *, now: float | None = None) -> dict | None:
    """当前有效心情（已衰减）；平静或太弱返回 None。"""
    moment = time.time() if now is None else now
    with context._lock:
        row = context._db().execute("SELECT * FROM mood WHERE group_id=?", (int(group_id),)).fetchone()
    if not row:
        return None
    intensity = decay(float(row["intensity"]), moment - float(row["updated_ts"]))
    name = str(row["mood"])
    if name == "平静" or intensity < MIN_INTENSITY:
        return None
    remain = HALF_LIFE * math.log2(intensity / MIN_INTENSITY) if intensity > 0 else 0.0
    return {
        "mood": name,
        "intensity": intensity,
        "reason": str(row["reason"]),
        "remain_min": max(0.0, remain / 60.0),
    }


def for_prompt(group_id: int, *, now: float | None = None) -> str:
    """回复时注入的"当前状态"块（可空）。"""
    m = get_mood(group_id, now=now)
    if not m:
        return ""
    reason = f"（{m['reason']}）" if m["reason"] else ""
    return (
        f"【你现在的状态】心情：{m['mood']}{reason}。"
        "语气自然带一点这个感觉即可，别刻意强调；被问到时可以自然说出来。"
    )


async def update_group(group_id: int, cfg: Config, *, now: float | None = None) -> bool:
    """综合最近互动更新心情；互动太少或解析失败返回 False。"""
    gid = int(group_id)
    moment = time.time() if now is None else now
    with context._lock:
        conn = context._db()
        row = conn.execute("SELECT * FROM mood WHERE group_id=?", (gid,)).fetchone()
        since = float(row["updated_ts"]) if row else moment - 3600.0
        rows = conn.execute(
            "SELECT name, text, ts FROM messages WHERE group_id=? AND ts>? ORDER BY id LIMIT 24",
            (gid, since),
        ).fetchall()
    batch = [dict(r) for r in rows]
    if len(batch) < _MIN_MESSAGES:
        return False
    current = f"当前心情：{row['mood']}（强度 {float(row['intensity']):.1f}）" if row else "当前心情：平静"
    lines = [
        f"{str(r.get('name') or '?')}: {str(r.get('text') or '').strip()}"
        for r in batch
        if str(r.get("text") or "").strip()
    ]
    material = current + "\n【最近群里的互动（旧→新）】\n" + "\n".join(lines)
    completion = await llm.chat_once(
        material,
        cfg.providers[llm.current_default()],
        session_key=f"qqbot-mood-{gid}",
        extra_system=_RULES,
    )
    parsed = parse_mood_update(completion.text or "")
    if not parsed:
        return False
    set_mood(gid, parsed["mood"], parsed["intensity"], parsed["reason"])
    return True
