"""好感度系统（借鉴 astrbot self-learning 的"守恒"设计）：

- 每个群每个用户一条好感度（0-100）；**群总上限 250**——想给谁加分，超出部分从其他人身上按比例扣（总好感守恒）。
- 变化由"交互类型"决定（规则关键词分类，零 LLM）：夸赞 +5 / 感谢 +2 / 普通聊天 +1 / 骂 -10 等；
  正向变化受**当前心情修正**（心情好时夸一句涨得多）。
- 长期不互动自然衰减（每 2 天 -1，在下次互动时结算）。
- 注入：回复时给"你和 TA 的好感度"块（聊天语气参考）；命令 /affection 查看/设置。
"""
from __future__ import annotations

import time

from . import context, mood

CAP_PER_USER = 100  # 单用户上限
CAP_TOTAL = 250  # 群总上限（守恒：超出从其他人身上扣）
DECAY_PER_2D = 1.0  # 每 2 天未互动自然衰减 1 点

# 交互规则（按顺序匹配，先查负面）：(关键词, 变化, 描述)
_RULES: tuple[tuple[tuple[str, ...], int, str], ...] = (
    (("去死", "傻逼", "傻b", "贱", "废物", "垃圾", "恶心"), -10, "攻击性言语"),
    (("滚", "闭嘴", "讨厌你", "烦死", "恨你"), -5, "不耐烦/嫌弃"),
    (("厉害", "太强", "好强", "666", "太棒", "好棒", "真棒", "优秀", "牛啊", "牛批", "牛逼", "绝了", "点赞"), 5, "夸赞"),
    (("谢谢", "感谢", "多谢", "辛苦", "爱你"), 2, "表达感谢"),
    (("早点睡", "注意身体", "还好吗", "没事吧", "别太累", "照顾好自己"), 2, "关心问候"),
)

# 心情修正系数（基础值 × (0.5 + 0.5×强度)）
_MOOD_MOD = {
    "开心": 1.2,
    "得意": 1.15,
    "好奇": 1.05,
    "疲惫": 0.9,
    "无语": 0.85,
    "委屈": 0.7,
    "恼火": 0.5,
}

_TIERS = (
    (80, "关系很好，聊起来可以更放松热络"),
    (50, "挺熟的，像老朋友一样随意"),
    (20, "普通群友，正常朋友语气"),
    (0, "还不太熟，自然一点别太自来熟"),
)


def classify(text: str) -> tuple[int, str]:
    """规则分类交互类型（零 LLM）：返回 (变化值, 描述)。"""
    t = text or ""
    for kws, delta, desc in _RULES:
        if any(k in t for k in kws):
            return delta, desc
    return 1, "普通聊天"


def _mood_modifier(group_id: int) -> float:
    try:
        m = mood.get_mood(group_id)
    except Exception:  # noqa: BLE001 —— 心情读取失败按平静算
        return 1.0
    if not m:
        return 1.0
    base = _MOOD_MOD.get(str(m["mood"]), 1.0)
    return base * (0.5 + 0.5 * float(m["intensity"]))


def update(group_id: int, user_id: int, name: str, text: str, *, now: float | None = None) -> dict:
    """处理一次互动：分类 → 心情修正 → 衰减结算 → 上限/守恒再分配。返回结果 dict。"""
    gid, uid = int(group_id), int(user_id)
    moment = time.time() if now is None else now
    delta, desc = classify(text)
    if delta > 0:
        delta = max(1, int(round(delta * _mood_modifier(gid))))
    with context._lock:
        conn = context._db()
        row = conn.execute("SELECT * FROM affection WHERE group_id=? AND user_id=?", (gid, uid)).fetchone()
        stored = int(row["level"]) if row else 0
        last = float(row["last_ts"]) if row else moment
        # 自然衰减结算（每 2 天 -1，在互动时一次性扣）
        absent_days = max((moment - last) / 86400.0, 0.0)
        decayed = min(int(absent_days / 2.0 * DECAY_PER_2D), stored)
        level = stored - decayed
        new_level = max(0, min(level + delta, CAP_PER_USER))
        gain = new_level - level
        # 群总上限守恒：超出部分从其他人身上扣
        if gain > 0:
            others = {
                int(o["user_id"]): int(o["level"])
                for o in conn.execute(
                    "SELECT user_id, level FROM affection WHERE group_id=? AND user_id!=?", (gid, uid)
                ).fetchall()
            }
            total_others = sum(others.values())
            remaining = level + total_others + gain - CAP_TOTAL
            while remaining > 0 and total_others > 0:
                progressed = False
                for uid_o in sorted(others, key=lambda k: -others[k]):
                    if remaining <= 0:
                        break
                    lv = others[uid_o]
                    if lv <= 0:
                        continue
                    take = min(lv, max(1, lv // 4), remaining)
                    others[uid_o] = lv - take
                    remaining -= take
                    progressed = True
                if not progressed:
                    break
            if remaining < (level + total_others + gain - CAP_TOTAL):
                for uid_o, lv in others.items():
                    conn.execute(
                        "UPDATE affection SET level=? WHERE group_id=? AND user_id=?", (lv, gid, uid_o)
                    )
            if remaining > 0:
                new_level = max(0, new_level - remaining)
                gain = new_level - level
        conn.execute(
            "INSERT INTO affection(group_id, user_id, name, level, last_ts) VALUES(?,?,?,?,?) "
            "ON CONFLICT(group_id, user_id) DO UPDATE SET name=excluded.name, level=excluded.level, "
            "last_ts=excluded.last_ts",
            (gid, uid, str(name or ""), new_level, moment),
        )
        conn.commit()
    return {
        "level": new_level,
        "change": new_level - stored,
        "delta": delta,
        "desc": desc,
        "decayed": decayed,
    }


def for_prompt(group_id: int, user_id: int, name: str = "") -> str:
    """回复时注入的"关系"块（无记录返回空串）。"""
    with context._lock:
        row = context._db().execute(
            "SELECT level FROM affection WHERE group_id=? AND user_id=?", (int(group_id), int(user_id))
        ).fetchone()
    if not row:
        return ""
    level = int(row["level"])
    tier = next(t for th, t in _TIERS if level >= th)
    who = f"「{name}」" if name else "对方"
    return f"【关系】你和{who}的好感度：{level}/100（{tier}）。"


def set_level(group_id: int, user_id: int, name: str, level: int, *, now: float | None = None) -> None:
    """管理员设置好感度。"""
    moment = time.time() if now is None else now
    lv = max(0, min(int(level), CAP_PER_USER))
    with context._lock:
        conn = context._db()
        conn.execute(
            "INSERT INTO affection(group_id, user_id, name, level, last_ts) VALUES(?,?,?,?,?) "
            "ON CONFLICT(group_id, user_id) DO UPDATE SET "
            "name=COALESCE(NULLIF(excluded.name,''), name), level=excluded.level, last_ts=excluded.last_ts",
            (int(group_id), int(user_id), str(name or ""), lv, moment),
        )
        conn.commit()


def top_list(group_id: int, limit: int = 10) -> list[dict]:
    with context._lock:
        rows = context._db().execute(
            "SELECT * FROM affection WHERE group_id=? ORDER BY level DESC LIMIT ?",
            (int(group_id), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def total(group_id: int) -> int:
    with context._lock:
        row = context._db().execute(
            "SELECT COALESCE(SUM(level),0) AS s FROM affection WHERE group_id=?", (int(group_id),)
        ).fetchone()
    return int(row["s"] or 0)
