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


def tool_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "send_sticker",
            "description": (
                "从图库里挑一张表情包/图片发到群里（斗图、接梗、表达情绪）。"
                "当你觉得发张图比说话更合适时调用；query 用简短词语描述想表达的意思，"
                "如：无语、笑死、点赞、委屈、猫猫困惑、得意。"
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
    q = _keywords(query)
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
