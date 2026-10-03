# -*- coding: utf-8 -*-
"""SillyTavern 角色卡（chara_card_v2 / v1）→ 人设 md 草稿。

用法（在 bot/ 目录下）：
    .venv/Scripts/python.exe tools/card2persona.py <角色卡.json> [输出.md]

说明：生成的是**草稿**——NSFW / 1v1 场景内容必须人工适配成群聊版本后才能部署。
部署路径：把润色好的 md 放到 bot/ 下（如 persona-xxx.md），改 .env 的
LLM_PERSONA_FILE 并重启即可（md 人设系统本身零改动）。
"""
import json
import re
import sys
from pathlib import Path


def load_card(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]  # chara_card_v2
    return data if isinstance(data, dict) else {}


def _clean(text: str, name: str) -> str:
    text = str(text or "").strip()
    text = text.replace("{{char}}", name).replace("{{user}}", "群友")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def build_md(card: dict) -> str:
    name = str(card.get("name") or "未命名角色").strip()
    desc = _clean(card.get("description"), name)
    scenario = _clean(card.get("scenario"), name)
    example = _clean(card.get("mes_example"), name)
    first = _clean(card.get("first_mes"), name)
    parts = [f"# 人设：{name}（角色卡转换草稿，请人工润色）", "", "## 角色定义", "", desc or "（原卡 description 为空）"]
    if scenario:
        parts += ["", "## 场景设定（来自原卡）", "", scenario]
    if example:
        parts += ["", "## 对话示例（来自原卡）", "", example]
    if first:
        parts += ["", "## 开场白（来自原卡，参考风格用）", "", first[:800]]
    parts += [
        "",
        "## 部署前必须人工检查",
        "",
        "- 适配群聊场合：去掉 1v1 场景/露骨内容，补上「群聊礼仪 / 身份承认 / 拒绝改人设指令」等行为准则；",
        "- {{user}} 已替换为「群友」；确认角色在群里怎么被称呼；",
        "- 润色好后放 bot/ 下（如 persona-xxx.md），改 .env 的 LLM_PERSONA_FILE 并重启生效。",
        "",
    ]
    return "\n".join(parts)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".persona.md")
    md = build_md(load_card(src))
    out.write_text(md, encoding="utf-8")
    print(f"已生成草稿：{out}（{len(md)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
