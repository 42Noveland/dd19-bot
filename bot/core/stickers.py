"""贴图库：从群消息收集的图片里挑表情包来回应（send_sticker 工具的后端）。

- 资源来自感知层（core.context.images 表）：群里出现过的图都被识别并打标（caption）。
- 挑选：query 关键词（CJK bigram + ASCII 词）与 caption 匹配，人气（seen_count）微加成；
  同群近期发过的图不再重复（10 分钟窗口）；完全匹配不上就不硬发（返回 None）。
- 发送记录暂存内存（进程级）；后续"斗图学习"（按反应加权）再加持久化。
"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path

from . import context

_recent_window = 600.0  # 同群重复抑制窗口（秒）
_last_sent: dict[int, list[tuple[str, float]]] = {}


def reset() -> None:
    """测试用：清空发送记录。"""
    _last_sent.clear()


def chat_hint() -> str:
    """挂载 send_sticker 时注入的系统提示：自然配图引导 + 综合挑选 + 频率自控。"""
    return (
        "【表情包】你的图库里有从群里收集的表情包。回复群友时，如果配一张表情包能更好地表达情绪或接梗"
        "（吐槽、无语、开心、安慰、得意、自嘲等），就调用 send_sticker 配一张——不用等对方要图；"
        "挑选时综合三样东西：你现在的心情、对方说的话、正在聊的话题；query 尽量用下面图库清单里的关键词。"
        "频率上克制一点：参考群聊记录里你最近发过的图，大约每 3~5 次回复最多配 1 张，别连着发。"
        "对方明确要图或要重发时必须配。"
    )


def library_summary(limit: int = 12) -> str:
    """图库清单（喂给模型挑 query）：序号 + 简短描述。"""
    try:
        rows = context.sticker_candidates(limit=limit)
    except Exception:  # noqa: BLE001
        return ""
    items: list[str] = []
    for i, row in enumerate(rows, 1):
        cap = str(row.get("caption") or "").strip()
        for prefix in ("这是一张", "一张", "图片中", "图中", "画面里", "画面"):
            if cap.startswith(prefix):
                cap = cap[len(prefix):]
                break
        cap = cap[:26]
        if cap:
            items.append(f"{i}.{cap}")
    return "；".join(items)


def tool_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "send_sticker",
            "description": (
                "给回复配一张表情包/图片发到群里（斗图、接梗、表达情绪、自嘲）。"
                "回复群友时不必等对方要图，觉得配一张更带感就调用；"
                "query 用简短词语描述想表达的情绪或梗（同时参考对方的话和你这条回复的基调），"
                "如：无语、笑死、点赞、委屈、懵了、得意。"
                "用户明确要求重发刚才那张时，把 repeat 设为 true。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "想表达的意思/情绪关键词"},
                    "repeat": {"type": "boolean", "description": "用户明确要求重发刚才发过的那张时设为 true"},
                },
                "required": ["query"],
            },
        },
    }


def _keywords(text: str) -> set[str]:
    grams: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(run) - 1):
            grams.add(run[i : i + 2])
    for word in re.findall(r"[A-Za-z0-9]+", text):
        grams.add(word.lower())
    return grams


_MOOD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("开心", "高兴", "哈哈", "笑死", "大笑", "乐呵", "美滋滋"),
    ("无语", "汗颜", "裂开", "麻了", "服了", "沉默"),
    ("委屈", "哭了", "流泪", "难过", "伤心", "呜呜"),
    ("生气", "气死", "火大", "恼火", "暴怒"),
    ("震惊", "懵", "惊了", "呆住", "傻眼"),
    ("得意", "拽", "骄傲", "傲娇", "嘿嘿"),
    ("喜欢", "心动", "比心", "爱心", "亲亲"),
    ("安慰", "抱抱", "摸摸", "拍拍"),
    ("加油", "冲鸭", "努力"),
)


def _expand_moods(q: set[str], text: str) -> set[str]:
    """同义情绪词扩展：query 命中某组任一词，并入该组全部词的关键词（提升小图库命中率）。"""
    for group in _MOOD_GROUPS:
        if any(word in text for word in group):
            for word in group:
                q |= _keywords(word)
    return q


def _recent_md5s(group_id: int) -> set[str]:
    now = time.time()
    rows = _last_sent.get(group_id, [])
    _last_sent[group_id] = [r for r in rows if now - r[1] < _recent_window]
    return {md5 for md5, _ in _last_sent[group_id]}


def pick(query: str, group_id: int, repeat: bool = False) -> dict | None:
    """按语义相似度挑一张图；仅当图库为空/文件全失效时返回 None。

    - repeat=True（用户明确要求重发）：不受防重窗口限制。
    - 无关键词匹配时随机发一张（模型既然调用了工具，说明该发图——宁发勿尬聊）。
    - 全部候选都在防重窗口内（小图库）时兜底允许重发。
    """
    query = (query or "").strip()
    candidates = [
        c
        for c in context.sticker_candidates()
        if str(c.get("path") or "") and Path(str(c["path"])).exists()
    ]
    if not candidates:
        return None
    recent = _recent_md5s(group_id)
    pool = candidates if repeat else [c for c in candidates if c["md5"] not in recent]
    if not pool:
        pool = candidates  # 小图库全被防重排除：宁发勿尬
    q = _expand_moods(_keywords(query), query)
    scored: list[tuple[int, float, float, dict]] = []
    for row in pool:
        caption = str(row.get("caption") or "")
        kw = len(q & _keywords(caption)) if q else 0
        pop = min(int(row.get("seen_count") or 1), 5) * 0.15
        scored.append((kw, pop, random.random(), row))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    if scored[0][0] <= 0:
        return random.choice(pool)  # 没有贴切的：随机发一张
    return scored[0][3]


def note_sent(group_id: int, md5: str) -> None:
    rows = _last_sent.setdefault(group_id, [])
    rows.append((md5, time.time()))
    del rows[:-10]
