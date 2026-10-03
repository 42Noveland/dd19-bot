"""群黑话（借鉴 astrbot self-learning）：零 LLM 统计预筛 + LLM 含义推断 + 注入。

- 预筛（纯本地）：把最近 DAYS 天的消息分词（CJK bigram + ASCII 词），过滤常用字组合后按四信号合成候选分——
  跨群 IDF(0.35) + 突发频率(0.25) + 用户集中度(0.2) + 字符互信息 PMI(0.2，区分真词与跨词碎片)；群内频次门槛 MIN_FREQ。
- 推断（mine_round）：每轮取候选（排除已入表的）最多 MAX_PER_ROUND 个，附上下文例子批量问 LLM；
  slang=false 的记为 rejected（不再重复问）。
- 注入（for_prompt）：消息含已知黑话 → 解释块（纪律：只用于理解，不复读/扩散）。
"""
from __future__ import annotations

import json
import math
import re
import time

from . import context, llm
from .config import Config

DAYS = 7.0  # 统计窗口
MIN_FREQ = 5  # 群内频次门槛
MAX_PER_ROUND = 6  # 每轮最多推断几个候选
MAX_KNOWN = 80  # 每群已知黑话上限
TOPK = 12  # 候选池大小
_REJECT_KEEP_DAYS = 30.0

# 常用字过滤：候选 bigram 含任一常用字 → 视为跨词碎片跳过（"来张""是十"这类）
_STOPCHARS = set(
    "的了在是我有和就不人都一上也很到说要去你会着没看好这那他她们吗吧呢啊哦嗯呀哈来对把让被给从还比得过可能为以而但或如与等及其之就十不了多又什怎哪谁些每么"
)

_RULES = (
    "【任务】你是群聊语言分析助手。下面是从一个 QQ 群最近聊天里筛出的高频词候选"
    "（群内频繁出现、其他群少见），每条附了上下文例子。请判断每个词在群里的实际含义：\n"
    '输出每行一个 JSON：{"term":"词","meaning":"不超过 20 字的含义","slang":true}\n'
    '如果它是普通词/人名/地名/口头禅等没有特殊含义的词，输出 {"term":"词","slang":false}；'
    "不确定含义时 meaning 留空。不要输出任何其他内容。"
)


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        for i in range(len(run) - 1):
            out.append(run[i : i + 2])
    for word in re.findall(r"[A-Za-z0-9]{3,}", text or ""):
        out.append(word.lower())
    return out


def _build_stats() -> tuple[dict[int, dict[str, dict]], dict[str, int], int]:
    """扫描最近 DAYS 天消息，返回 ({gid: {term: {"freq","users","first_ts","contexts"}}}, 全库字频, 总字数)。"""
    cutoff = time.time() - DAYS * 86400
    with context._lock:
        conn = context._db()
        rows = conn.execute(
            "SELECT group_id, user_id, text, ts FROM messages WHERE ts>=? ORDER BY id", (cutoff,)
        ).fetchall()
    stats: dict[int, dict[str, dict]] = {}
    chars: dict[str, int] = {}
    total = 0
    for r in rows:
        text = str(r["text"] or "")
        if not text or text.startswith("["):
            continue
        per = stats.setdefault(int(r["group_id"]), {})
        for run in re.findall(r"[\u4e00-\u9fff]+", text):
            total += len(run)
            for ch in run:
                chars[ch] = chars.get(ch, 0) + 1
        for term in _tokens(text):
            it = per.get(term)
            if it is None:
                it = {"freq": 0, "users": set(), "first_ts": float(r["ts"]), "contexts": []}
                per[term] = it
            it["freq"] += 1
            it["users"].add(int(r["user_id"]))
            if len(it["contexts"]) < 3:
                it["contexts"].append(text[:60])
    return stats, chars, total


def candidates(group_id: int, top: int = TOPK, exclude: set[str] | None = None) -> list[dict]:
    """四信号合成候选分（IDF 0.35 + 突发 0.25 + 集中度 0.2 + PMI 0.2），频次 ≥ MIN_FREQ。"""
    stats, chars, total = _build_stats()
    mine = stats.get(int(group_id), {})
    if not mine:
        return []
    exclude = exclude or set()
    num_groups = max(len(stats), 1)
    denom = math.log(max(num_groups, 2)) or 1.0
    now = time.time()
    out: list[dict] = []
    for term, it in mine.items():
        if it["freq"] < MIN_FREQ or term in exclude:
            continue
        if len(term) == 2 and (term[0] in _STOPCHARS or term[1] in _STOPCHARS):
            continue  # 常用字组合：跨词碎片
        groups_containing = sum(1 for g in stats.values() if term in g)
        idf = math.log(num_groups / max(groups_containing, 1)) / denom
        age_days = max((now - it["first_ts"]) / 86400.0, 1.0)
        burst = min(it["freq"] / age_days, 5.0) / 5.0
        conc = 1.0 / max(len(it["users"]), 1)
        # 字符互信息：真词远高于跨词碎片（0-10 归一化）
        if len(term) == 2:
            cx, cy = chars.get(term[0], 0), chars.get(term[1], 0)
            pmi = math.log2(it["freq"] * total / (cx * cy)) if cx and cy and total else 0.0
        else:
            pmi = 6.0  # ASCII 词直接给中位分
        pmi_norm = min(max(pmi, 0.0), 10.0) / 10.0
        score = 0.35 * idf + 0.25 * burst + 0.2 * conc + 0.2 * pmi_norm
        out.append({
            "term": term,
            "score": round(score, 4),
            "freq": it["freq"],
            "idf": round(idf, 3),
            "burst": round(burst, 3),
            "pmi": round(pmi, 1),
            "users": len(it["users"]),
            "contexts": it["contexts"],
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:top]


def parse_round_reply(raw: str) -> list[dict]:
    """解析推断输出（容错：代码块包裹/垃圾行/整行数组）。返回 [{"term","meaning","slang"}]。"""
    out: list[dict] = []
    for line in (raw or "").splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        for it in obj if isinstance(obj, list) else [obj]:
            if not isinstance(it, dict):
                continue
            term = str(it.get("term") or "").strip()
            if not term:
                continue
            out.append({
                "term": term,
                "meaning": str(it.get("meaning") or "").strip()[:40],
                "slang": bool(it.get("slang")),
            })
    return out


def _known_count(gid: int) -> int:
    with context._lock:
        conn = context._db()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jargon WHERE group_id=? AND status='known'", (int(gid),)
        ).fetchone()
    return int(row["n"] or 0)


def _save(gid: int, item: dict, freq: int, now: float) -> str:
    if item["slang"] and item["meaning"]:
        status, meaning = "known", item["meaning"]
    else:
        status, meaning = "rejected", ""
    with context._lock:
        conn = context._db()
        conn.execute(
            "INSERT INTO jargon(group_id, term, meaning, status, freq, created_ts, updated_ts) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(group_id, term) DO UPDATE SET meaning=excluded.meaning, status=excluded.status, "
            "freq=excluded.freq, updated_ts=excluded.updated_ts",
            (int(gid), item["term"], meaning, status, int(freq), now, now),
        )
        conn.commit()
    return status


def _prune(gid: int) -> None:
    cutoff = time.time() - _REJECT_KEEP_DAYS * 86400
    with context._lock:
        conn = context._db()
        conn.execute(
            "DELETE FROM jargon WHERE group_id=? AND status='rejected' AND updated_ts < ?", (int(gid), cutoff)
        )
        conn.execute(
            "DELETE FROM jargon WHERE group_id=? AND status='known' AND id NOT IN ("
            "SELECT id FROM jargon WHERE group_id=? AND status='known' ORDER BY updated_ts DESC LIMIT ?)",
            (int(gid), int(gid), MAX_KNOWN),
        )
        conn.commit()


async def mine_round(group_id: int, cfg: Config) -> dict:
    """挖一轮：预筛候选 → LLM 推断含义 → 存库。返回 {"candidates","saved","known"}。"""
    gid = int(group_id)
    with context._lock:
        conn = context._db()
        existing = {str(r["term"]) for r in conn.execute("SELECT term FROM jargon WHERE group_id=?", (gid,)).fetchall()}
    cands = candidates(gid, exclude=existing)
    if not cands:
        return {"candidates": 0, "saved": 0, "known": _known_count(gid)}
    batch = cands[:MAX_PER_ROUND]
    lines: list[str] = []
    for i, c in enumerate(batch, 1):
        ex = "」「".join(c["contexts"][:3])
        lines.append(f'{i}. 「{c["term"]}」（群内出现 {c["freq"]} 次）例：「{ex}」')
    completion = await llm.chat_once(
        "候选：\n" + "\n".join(lines),
        cfg.providers[llm.current_default()],
        session_key=f"qqbot-jargon-{gid}",
        extra_system=_RULES,
    )
    items = parse_round_reply(completion.text)
    freq_map = {c["term"]: c["freq"] for c in batch}
    saved = 0
    now = time.time()
    for it in items:
        if it["term"] not in freq_map:
            continue  # 只收本轮候选里的词
        _save(gid, it, freq_map[it["term"]], now)
        saved += 1
    _prune(gid)
    return {"candidates": len(cands), "saved": saved, "known": _known_count(gid)}


def list_known(group_id: int, limit: int = 10) -> list[tuple[str, str]]:
    with context._lock:
        conn = context._db()
        rows = conn.execute(
            "SELECT term, meaning FROM jargon WHERE group_id=? AND status='known' "
            "ORDER BY updated_ts DESC LIMIT ?",
            (int(group_id), int(limit)),
        ).fetchall()
    return [(str(r["term"]), str(r["meaning"])) for r in rows]


def for_prompt(group_id: int, text: str) -> str:
    """消息含已知黑话 → 解释块；没有则返回空串。"""
    t = text or ""
    if len(t) < 2:
        return ""
    with context._lock:
        conn = context._db()
        rows = conn.execute(
            "SELECT term, meaning FROM jargon WHERE group_id=? AND status='known' "
            "ORDER BY updated_ts DESC LIMIT ?",
            (int(group_id), MAX_KNOWN),
        ).fetchall()
    hits = [(str(r["term"]), str(r["meaning"])) for r in rows if r["meaning"] and str(r["term"]) in t]
    if not hits:
        return ""
    lines = [
        "[黑话理解] 以下黑话解释只用于理解用户当前消息。回复时不要主动复读、模仿、扩散这些黑话，"
        "也不要把解释原样输出；仅在用户明确询问含义时才说明。"
    ]
    for term, meaning in hits[:5]:
        lines.append(f"- 「{term}」≈ {meaning}")
    return "\n".join(lines)


def stats_for(group_id: int) -> dict:
    gid = int(group_id)
    with context._lock:
        conn = context._db()
        known = conn.execute(
            "SELECT COUNT(*) AS n FROM jargon WHERE group_id=? AND status='known'", (gid,)
        ).fetchone()["n"]
        rejected = conn.execute(
            "SELECT COUNT(*) AS n FROM jargon WHERE group_id=? AND status='rejected'", (gid,)
        ).fetchone()["n"]
    return {"known": int(known or 0), "rejected": int(rejected or 0)}
