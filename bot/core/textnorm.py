"""回复文本规范化（借鉴 MaiBot 的输出清洗层）。

- `clean_reply`：删除 LLM 回复里**含中文的括号内容**（（）/()/【】/[]）——防角色扮演旁白
  （"（笑）""（心想：…）""（配了一张治愈系的小图…）"）这类真人不会发的东西；
  清洗后为空时回退 "呃呃"（同 MaiBot）。纯英文括号（如 "(v1.2)" "(hhh)"）保留。
- 与 MaiBot 的差异（有意）：它的正则只要"括号后有中文"就删到最近的闭括号（可能误伤纯英文括号），
  我们收紧为"括号**内容**含中文才删"。
- 只清洗模型生成的聊天回复；我们自己追加的提示（截断说明等）不经过清洗。
"""
from __future__ import annotations

import re

# 含中文的括号内容：整段删除。开启符（(【[ → 括号内（不含任何闭括号的范围内）出现中文 → 删除到对应闭括号
_BRACKET_CONTENT = re.compile(r"[（(【\[](?=[^）)】\]]*[\u4e00-\u9fff])[^）)】\]]*[）)】\]]")

_EMPTY_FALLBACK = "呃呃"


def looks_like_tool_leak(text: str) -> bool:
    """检测模型把工具调用序列化成了文本（DeepSeek DSML 泄漏 / 旧式 tool▁calls 标记）。

    实测事故：模型偶发输出 "<｜｜DSML｜｜ invoke name=\"send_message\">..." 这类伪工具调用
    文本（未走结构化 tool_calls），若当正文发送会把乱码发进群。命中即视为异常输出：
    绝不按正常回复放行（兜底策略——宁可不回/回一句人话，不发乱码）。
    """
    t = str(text or "")
    if not t:
        return False
    low = t.lower()
    if "dsml" in low and ("<" in t or "｜" in t):
        return True
    return "tool▁calls" in low


def clean_reply(text: str) -> str:
    """清洗聊天回复文本（纯函数）：删含中文括号内容；清空后回退"呃呃"。"""
    t = str(text or "")
    if not t:
        return t
    cleaned = _BRACKET_CONTENT.sub("", t)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return _EMPTY_FALLBACK
    return cleaned
