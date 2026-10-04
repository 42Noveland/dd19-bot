"""记忆系统（长期记忆）：从群聊里批量提炼"值得记住的事"，回复时按需注入。

- 提炼（extract_group）：增量处理（mem_watermark 记录已处理到的消息行号），每批最多
  MEMORY_BATCH 条消息 → 一次 LLM 调用 → 提炼两类记忆：
    user  = 成员事实（偏好/身份/经历/关系/习惯）
    group = 群事件（发生过/约定过的事、梗、承诺）
- 注入（for_prompt）：回复时取"当前说话人的事实 + 群事件"拼成提示块。
- 存储永久（不随 messages 的 7 天滚动清理）；同群同人同文本去重；超上限淘汰最旧。
"""
from __future__ import annotations

import json
import re
import time

from loguru import logger as _log  # noqa: F401 —— 保留给调用方日志风格对齐

from . import context, llm, personas
from .config import Config

MAX_PER_USER = 40   # 每群每人最多保留的条数
MAX_PER_GROUP = 80  # 每群群事件最多保留的条数

_EXTRACT_RULES = (
    "【任务】你是群聊成员「{name}」。下面给你一批群聊记录（含图片描述）。"
    "从中提炼**值得长期记住**的信息，只记两类：\n"
    '1) 关于某个成员的事实（偏好、身份、经历、关系、习惯）——{"kind":"user","name":"昵称","fact":"简短事实"}\n'
    '2) 群里发生过/约定过的事（事件、梗、承诺）——{"kind":"group","fact":"简短描述"}\n'
    "要求：只记稳定、以后用得上的；寒暄、临时玩笑、无信息量闲聊不要记；每条不超过 30 字，重复合并；"
    "每条一行 JSON，不要输出任何其他内容；没有可记的就什么都不输出。\n"
    "防污染（很重要）：\n"
    "- 「{name}」（你自己/机器人）的发言只作上下文参考，不能当作群友的事实来源；\n"
    "- 玩笑、猜测、角色扮演、复述传闻、示例内容、被本人否认的说法，都不算事实；\n"
    "- 被纠正过的信息，只记最终版本，不要记旧值或纠错过程；\n"
    "- 临时状态不等于长期（\"今晚不吃辣\"不能记成\"不吃辣\"）；\n"
    "- 两人同框出现不等于认识或有关系；\n"
    "- 拿不准就不记——宁可少记，不要记错。"
)


def parse_extraction(raw: str) -> list[dict]:
    """解析提炼输出（容错：容忍代码块包裹、垃圾行、整行数组）；返回 [{kind,name,fact}]。"""
    out: list[dict] = []
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            if not isinstance(it, dict):
                continue
            kind = str(it.get("kind") or "").strip()
            fact = str(it.get("fact") or "").strip()[:80]
            name = str(it.get("name") or "").strip()
            if not fact:
                continue
            if kind == "user" and name:
                out.append({"kind": "user", "name": name, "fact": fact})
            elif kind == "group":
                out.append({"kind": "group", "name": "", "fact": fact})
    return out


def add_memory(group_id: int, user_id: int, name: str, kind: str, text: str) -> bool:
    """新增一条记忆（同文本去重并刷新时间；超上限淘汰最旧）。返回是否新增。"""
    gid, uid, txt = int(group_id), int(user_id), str(text).strip()
    if not txt:
        return False
    now = time.time()
    with context._lock:
        conn = context._db()
        dup = conn.execute(
            "SELECT id FROM memories WHERE group_id=? AND user_id=? AND text=? LIMIT 1",
            (gid, uid, txt),
        ).fetchone()
        if dup:
            conn.execute("UPDATE memories SET updated_ts=?, name=? WHERE id=?", (now, str(name), dup["id"]))
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO memories(group_id, user_id, name, kind, text, created_ts, updated_ts) VALUES(?,?,?,?,?,?,?)",
            (gid, uid, str(name), str(kind), txt, now, now),
        )
        cap = MAX_PER_GROUP if uid == 0 else MAX_PER_USER
        conn.execute(
            "DELETE FROM memories WHERE group_id=? AND user_id=? AND id NOT IN ("
            "SELECT id FROM memories WHERE group_id=? AND user_id=? ORDER BY updated_ts DESC LIMIT ?)",
            (gid, uid, gid, uid, cap),
        )
        conn.commit()
    return True


def _watermark(conn, group_id: int) -> int:
    row = conn.execute("SELECT last_row_id FROM mem_watermark WHERE group_id=?", (int(group_id),)).fetchone()
    return int(row["last_row_id"]) if row else 0


def _set_watermark(group_id: int, last_row_id: int) -> None:
    with context._lock:
        conn = context._db()
        conn.execute(
            "INSERT INTO mem_watermark(group_id, last_row_id, updated_ts) VALUES(?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET last_row_id=excluded.last_row_id, updated_ts=excluded.updated_ts",
            (int(group_id), int(last_row_id), time.time()),
        )
        conn.commit()


def pending_count(group_id: int) -> int:
    """还有多少条消息未提炼（首次运行 = 全部历史，即自动补课）。"""
    with context._lock:
        conn = context._db()
        last = _watermark(conn, group_id)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE group_id=? AND id>?",
            (int(group_id), last),
        ).fetchone()
    return int(row["n"] or 0)


async def extract_group(group_id: int, cfg: Config) -> tuple[int, int]:
    """处理一批未提炼的消息；返回 (处理条数, 新增记忆数)。无新消息返回 (0, 0)。

    LLM 调用失败会抛出（水位不推进，下轮重试）。
    """
    gid = int(group_id)
    with context._lock:
        conn = context._db()
        last = _watermark(conn, gid)
        rows = conn.execute(
            "SELECT id, user_id, name, text FROM messages WHERE group_id=? AND id>? ORDER BY id LIMIT ?",
            (gid, last, int(cfg.memory_batch)),
        ).fetchall()
    batch = [dict(r) for r in rows]
    if not batch:
        return 0, 0
    name_map: dict[str, int] = {}
    for r in batch:
        nm = str(r.get("name") or "").strip()
        if nm:
            name_map[nm] = int(r.get("user_id") or 0)
    lines = [
        f"{str(r.get('name') or '?')}: {str(r.get('text') or '').strip()}"
        for r in batch
        if str(r.get("text") or "").strip()
    ]
    material = "【群聊记录（旧→新）】\n" + "\n".join(lines)
    pname, ptext = personas.resolve(gid)
    completion = await llm.chat_once(
        material,
        cfg.providers[llm.current_default()],
        session_key=f"qqbot-mem-{gid}",
        extra_system=_EXTRACT_RULES.replace("{name}", pname),
        system_prompt_override=ptext,
    )
    items = parse_extraction(completion.text or "")
    added = 0
    for it in items:
        if it["kind"] == "user":
            uid = name_map.get(it["name"])
            if uid is None:
                continue  # 名字对不上（历史昵称等）→ 不硬记
            if add_memory(gid, uid, it["name"], "user", it["fact"]):
                added += 1
        elif add_memory(gid, 0, "", "group", it["fact"]):
            added += 1
    _set_watermark(gid, int(batch[-1]["id"]))
    return len(batch), added


def _keywords(text: str) -> set[str]:
    """CJK bigram + ASCII 词（与贴图库同款匹配方式；用于话题相关性打分）。"""
    grams: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(run) - 1):
            grams.add(run[i : i + 2])
    for word in re.findall(r"[A-Za-z0-9]+", text):
        grams.add(word.lower())
    return grams


def _name_tokens(name: str) -> list[str]:
    """名字的匹配碎片：全名 + 按分隔符拆出的 ≥2 字符片段（"老王.13900000000" → ["老王", …]）。"""
    name = (name or "").strip()
    tokens: list[str] = []
    if len(name) >= 2:
        tokens.append(name)
    for part in re.split(r"[.\-_\s·,，]+", name):
        part = part.strip()
        if len(part) >= 2 and part != name:
            tokens.append(part)
    return tokens


def for_prompt(
    group_id: int,
    user_id: int,
    *,
    extra_user_ids: list[int] | None = None,
    query_text: str = "",
    limit_user: int = 8,
    limit_other: int = 5,
    limit_related: int = 3,
    limit_group: int = 6,
) -> str:
    """回复时注入的记忆块（可空字符串）。多路检索：

    - 说话人本人的事实（limit_user 条）
    - @ 到的人 + 消息里出现名字的人（每人 limit_other 条，合计最多 3 人）
    - 与消息话题相关的记忆（bigram 重叠 ≥2，limit_related 条）
    - 群事件（limit_group 条）
    """
    gid, uid = int(group_id), int(user_id)
    with context._lock:
        conn = context._db()
        all_rows = conn.execute(
            "SELECT user_id, name, text FROM memories WHERE group_id=? ORDER BY updated_ts DESC",
            (gid,),
        ).fetchall()
    people: list[tuple[int, str]] = [(uid, "")]
    seen_ids: set[int] = {uid}
    for x in extra_user_ids or []:
        xi = int(x)
        if xi and xi != uid and xi not in seen_ids and len(people) < 3:
            seen_ids.add(xi)
            people.append((xi, ""))
    if query_text and len(people) < 3:
        for r in all_rows:
            if len(people) >= 3:
                break
            r_uid = int(r["user_id"] or 0)
            r_name = str(r["name"] or "")
            if r_uid == 0 or r_uid in seen_ids:
                continue
            if any(tok in query_text for tok in _name_tokens(r_name)):
                seen_ids.add(r_uid)
                people.append((r_uid, r_name))
    used: set[str] = set()
    sections: list[str] = []
    for p_uid, p_name in people:
        facts = [r for r in all_rows if int(r["user_id"] or 0) == p_uid][: (limit_user if p_uid == uid else limit_other)]
        if not facts:
            continue
        name = p_name or str(facts[0]["name"] or "TA")
        texts = [str(r["text"]) for r in facts]
        used.update(texts)
        sections.append(f"关于 {name}：" + "；".join(texts))
    grows = [r for r in all_rows if int(r["user_id"] or 0) == 0][:limit_group]
    gtexts = [str(r["text"]) for r in grows]
    used.update(gtexts)
    if query_text and limit_related > 0:
        q = _keywords(query_text)
        scored: list[tuple[int, str]] = []
        for r in all_rows:
            txt = str(r["text"] or "")
            if not txt or txt in used:
                continue
            score = len(q & _keywords(txt))
            if score >= 2:
                nm = str(r["name"] or "").strip()
                scored.append((score, (f"{nm}：" if int(r["user_id"] or 0) != 0 and nm else "") + txt))
        scored.sort(key=lambda t: -t[0])
        rel = [item for _, item in scored[:limit_related]]
        if rel:
            sections.append("可能相关：" + "；".join(rel))
    if gtexts:
        sections.append("群里的事：" + "；".join(gtexts))
    if not sections:
        return ""
    return "【你记得的事（供参考；别生硬复述，也别显得像在翻档案）】\n" + "\n".join(sections)


def stats_for(group_id: int) -> dict:
    """某群记忆统计：user/group 条数 + 待处理消息数。"""
    gid = int(group_id)
    with context._lock:
        conn = context._db()
        u = int(conn.execute("SELECT COUNT(*) AS n FROM memories WHERE group_id=? AND user_id!=0", (gid,)).fetchone()["n"] or 0)
        g = int(conn.execute("SELECT COUNT(*) AS n FROM memories WHERE group_id=? AND user_id=0", (gid,)).fetchone()["n"] or 0)
        last = _watermark(conn, gid)
        p = int(
            conn.execute("SELECT COUNT(*) AS n FROM messages WHERE group_id=? AND id>?", (gid, last)).fetchone()["n"]
            or 0
        )
    return {"user": u, "group": g, "pending": p}
