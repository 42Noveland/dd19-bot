"""活体探针：验证软糯少女版人设的说话风格（语气词密度 + 软糯表达）。"""
import asyncio
import os
import re
import sys
import types
from pathlib import Path

BOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BOT))
os.chdir(BOT)

import nonebot
from nonebot.config import Config

nonebot._driver = types.SimpleNamespace(config=Config())

from core import llm  # noqa: E402
from core.config import load_config  # noqa: E402
import core.config as core_config  # noqa: E402

env: dict[str, str] = {}
for line in (BOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
cfg = load_config(env)
core_config._config = cfg

PERSONA = (BOT / "persona-whale.md").read_text(encoding="utf-8")

SCENES = [
    ("被夸", "你好可爱"),
    ("被凶", "你就是个大笨蛋"),
    ("群友难过", "我今天好难过"),
    ("吃醋", "你看隔壁那只猫多可爱"),
    ("被冷落", "（半小时没理）在吗"),
    ("陌生人搭话", "你好呀，能认识一下吗"),
    ("被叫大肥鱼", "蓝色大肥鱼！"),
]

PARTICLES = re.compile(r"[嘛呀啦哦呢呜嘿哈～]")


async def main() -> None:
    for tag, text in SCENES:
        try:
            r = await llm.chat(
                text,
                session_key="probe-soft-persona",
                system_prompt_override=PERSONA,
                is_replied=None,
            )
            t = (r.text or "").strip().replace("\n", " / ")
            n = len(PARTICLES.findall(t))
            print(f"[{tag}] {text}\n  -> {t[:140]}\n     语气词 {n} 个\n", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{tag}] EXC {type(e).__name__}: {e}\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
