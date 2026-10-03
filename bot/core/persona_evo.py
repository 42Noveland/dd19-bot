"""人格演化审查（借鉴 astrbot self-learning 的 pending→批准→回滚）：

- 生成（generate_proposals）：以本群风格对 + 记忆为证据，让 LLM 提"人设补充建议"（口语化命令式，最多 3 条）
  → 进待审队列（pending）；同一人设去重；**管理员批准前不生效**。
- 审查（approve/reject）：批准 → 写入 persona_patches（**不改 persona*.md 原文**）；拒绝 → 标记 rejected。
- 生效：personas.resolve 组装时把已批准补充拼在人设文本尾部（/persona patch rm 可回滚）。
"""
from __future__ import annotations

import json
import time

from . import context, llm, style_pairs
from .config import Config

MAX_PROPOSALS = 3  # 每轮最多几条建议
MIN_NEW_PAIRS = 15  # 距上次生成至少新增这么多风格对才值得再问（后台循环用）
_MAX_PENDING = 10  # 每群待审上限（超了不再生成）

_RULES = (
    "【任务】你是人设维护助手。下面是某个群机器人当前的人设文本、它最近在群里的真实表现"
    "（对话样例）和记忆。请判断有没有**值得写进人设的稳定补充**（表达习惯、话题偏好、互动方式）：\n"
    '输出每行一个 JSON：{"text":"建议"}\n'
    "要求：1) 每条是一句**口语化命令式**建议（'多…''说话要…''别…'），不超过 40 字；\n"
    "2) 只提有据可依、且人设里还没有的；3) 最多 3 条；没有值得补充的就什么都不输出。不要输出任何其他内容。"
)


def parse_proposals(raw: str) -> list[str]:
    """解析生成输出（容错：代码块包裹/垃圾行/整行数组）。返回 [text, ...]。"""
    out: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        for it in obj if isinstance(obj, list) else [obj]:
            if isinstance(it, dict):
                t = str(it.get("text") or "").strip()[:60]
                if t:
                    out.append(t)
    return out


def _last_round_ts(gid: int) -> float:
    with context._lock:
        row = context._db().execute(
            "SELECT MAX(created_ts) AS ts FROM persona_proposals WHERE group_id=?", (int(gid),)
        ).fetchone()
    return float(row["ts"]) if row and row["ts"] else 0.0


def new_pairs_since_last(gid: int) -> int:
    """距上次生成以来新增的风格对数（后台循环的触发判据）。"""
    ts = _last_round_ts(gid)
    with context._lock:
        row = context._db().execute(
            "SELECT COUNT(*) AS n FROM style_pairs WHERE group_id=? AND create_ts>?", (int(gid), ts)
        ).fetchone()
    return int(row["n"] or 0)


def pending_count(gid: int) -> int:
    with context._lock:
        row = context._db().execute(
            "SELECT COUNT(*) AS n FROM persona_proposals WHERE group_id=? AND status='pending'", (int(gid),)
        ).fetchone()
    return int(row["n"] or 0)


def list_pending(gid: int) -> list[dict]:
    with context._lock:
        rows = context._db().execute(
            "SELECT id, text FROM persona_proposals WHERE group_id=? AND status='pending' ORDER BY id",
            (int(gid),),
        ).fetchall()
    return [dict(r) for r in rows]


def list_patches(gid: int) -> list[dict]:
    with context._lock:
        rows = context._db().execute(
            "SELECT id, text FROM persona_patches WHERE group_id=? ORDER BY id", (int(gid),)
        ).fetchall()
    return [dict(r) for r in rows]


def patches_for(gid: int, persona: str) -> list[tuple[int, str]]:
    """personas.resolve 用：该群该人设已生效的补充 [(id, text), ...]。"""
    with context._lock:
        rows = context._db().execute(
            "SELECT id, text FROM persona_patches WHERE group_id=? AND persona=? ORDER BY id",
            (int(gid), str(persona)),
        ).fetchall()
    return [(int(r["id"]), str(r["text"])) for r in rows]


def add_pending(gid: int, persona: str, text: str) -> int:
    """直接注入一条待审建议（测试/探针用）。"""
    now = time.time()
    with context._lock:
        conn = context._db()
        cur = conn.execute(
            "INSERT INTO persona_proposals(group_id, persona, text, status, created_ts, updated_ts) "
            "VALUES(?,?,?,?,?,?)",
            (int(gid), str(persona), str(text).strip(), "pending", now, now),
        )
        conn.commit()
    return int(cur.lastrowid or 0)


async def generate_proposals(group_id: int, cfg: Config) -> dict:
    """生成一轮待审建议；返回 {"generated","pending"}。"""
    from . import personas  # 延迟导入避免与 personas.resolve 形成环

    gid = int(group_id)
    name, persona_text = personas.resolve(gid)
    pairs = style_pairs.list_pairs(gid, persona=name, limit=15)
    if not pairs:
        return {"generated": 0, "pending": pending_count(gid)}
    if pending_count(gid) >= _MAX_PENDING:
        return {"generated": 0, "pending": pending_count(gid)}
    with context._lock:
        conn = context._db()
        mems = [str(r["text"]) for r in conn.execute(
            "SELECT text FROM memories WHERE group_id=? ORDER BY updated_ts DESC LIMIT 10", (gid,)
        ).fetchall()]
    lines = [f"当前人设（{name}）：", persona_text[:1200], "", "最近的对话样例："]
    for r in pairs[:15]:
        lines.append(f"- 对方说「{r['situation']}」→ 它回「{r['expression'][:70]}」")
    if mems:
        lines.append("")
        lines.append("记忆：")
        lines.extend(f"- {m}" for m in mems)
    completion = await llm.chat_once(
        "\n".join(lines),
        cfg.providers[llm.current_default()],
        session_key=f"qqbot-poevo-{gid}",
        extra_system=_RULES,
    )
    texts = parse_proposals(completion.text)[:MAX_PROPOSALS]
    saved = 0
    if texts:
        now = time.time()
        with context._lock:
            conn = context._db()
            existing = {str(r["text"]) for r in conn.execute(
                "SELECT text FROM persona_proposals WHERE group_id=? AND status IN ('pending','approved')",
                (gid,),
            ).fetchall()}
            applied = {str(r["text"]) for r in conn.execute(
                "SELECT text FROM persona_patches WHERE group_id=?", (gid,)
            ).fetchall()}
            for t in texts:
                if t in existing or t in applied:
                    continue
                conn.execute(
                    "INSERT INTO persona_proposals(group_id, persona, text, status, created_ts, updated_ts) "
                    "VALUES(?,?,?,?,?,?)",
                    (gid, name, t, "pending", now, now),
                )
                saved += 1
            conn.commit()
    return {"generated": saved, "pending": pending_count(gid)}


def approve(proposal_id: int) -> dict:
    """批准：写入生效补充（不改 md 原文）。"""
    now = time.time()
    with context._lock:
        conn = context._db()
        row = conn.execute("SELECT * FROM persona_proposals WHERE id=?", (int(proposal_id),)).fetchone()
        if not row or row["status"] != "pending":
            return {"ok": False, "reason": "没找到这条待审建议（可能已处理过）"}
        conn.execute(
            "UPDATE persona_proposals SET status='approved', updated_ts=? WHERE id=?", (now, int(proposal_id))
        )
        conn.execute(
            "INSERT INTO persona_patches(group_id, persona, text, proposal_id, created_ts) VALUES(?,?,?,?,?)",
            (int(row["group_id"]), str(row["persona"]), str(row["text"]), int(proposal_id), now),
        )
        conn.commit()
    return {"ok": True, "group_id": int(row["group_id"]), "text": str(row["text"])}


def reject(proposal_id: int) -> dict:
    with context._lock:
        conn = context._db()
        row = conn.execute("SELECT * FROM persona_proposals WHERE id=?", (int(proposal_id),)).fetchone()
        if not row or row["status"] != "pending":
            return {"ok": False, "reason": "没找到这条待审建议（可能已处理过）"}
        conn.execute(
            "UPDATE persona_proposals SET status='rejected', updated_ts=? WHERE id=?",
            (time.time(), int(proposal_id)),
        )
        conn.commit()
    return {"ok": True, "text": str(row["text"])}


def remove_patch(patch_id: int) -> dict:
    with context._lock:
        conn = context._db()
        row = conn.execute("SELECT * FROM persona_patches WHERE id=?", (int(patch_id),)).fetchone()
        if not row:
            return {"ok": False, "reason": "没找到这条补充"}
        conn.execute("DELETE FROM persona_patches WHERE id=?", (int(patch_id),))
        conn.commit()
    return {"ok": True, "text": str(row["text"])}


async def handle_command(group_id: int, parts: list[str], cfg: Config) -> str:
    """处理 /persona 的审查子命令；返回要回复的文本。"""
    gid = int(group_id)
    cmd = parts[0].lower() if parts else ""
    if cmd == "review":
        rows = list_pending(gid)
        if not rows:
            return "没有待审建议。用法：/persona evolve 生成一轮建议"
        lines = ["待审的人设补充建议（/persona approve <id> 批准；/persona reject <id> 拒绝）："]
        for r in rows:
            lines.append(f"[{r['id']}] {r['text']}")
        return "\n".join(lines)
    if cmd == "approve" and len(parts) > 1 and parts[1].isdigit():
        r = approve(int(parts[1]))
        return (
            f"已批准并生效：{r['text']}（下一条回复起带上；/persona patches 可查看、/persona patch rm 可回滚）"
            if r["ok"] else r["reason"]
        )
    if cmd == "reject" and len(parts) > 1 and parts[1].isdigit():
        r = reject(int(parts[1]))
        return f"已拒绝：{r['text']}" if r["ok"] else r["reason"]
    if cmd == "patches":
        rows = list_patches(gid)
        if not rows:
            return "本群还没有生效的学习补充"
        lines = ["本群已生效的人设补充（/persona patch rm <id> 回滚）："]
        for r in rows:
            lines.append(f"[{r['id']}] {r['text']}")
        return "\n".join(lines)
    if cmd == "patch" and len(parts) > 2 and parts[1] == "rm" and parts[2].isdigit():
        r = remove_patch(int(parts[2]))
        return f"已回滚补充 [{parts[2]}]，下一条回复起不再生效" if r["ok"] else r["reason"]
    if cmd == "evolve":
        r = await generate_proposals(gid, cfg)
        return f"生成完成：新增待审 {r['generated']} 条（当前待审 {r['pending']} 条）。/persona review 查看"
    return "未知子命令。可用：review / approve <id> / reject <id> / evolve / patches / patch rm <id>"
