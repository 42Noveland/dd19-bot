"""NoneBot2 入口：初始化、注册 OneBot v11 适配器、加载 plugins/。

运行方式（工作目录必须是本目录 bot/）：
    .venv/Scripts/python.exe bot.py
或直接双击 start-bot.cmd。
"""
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

_BASE = Path(__file__).resolve().parent

# 从 bot/.env 读取配置（绝对路径，避免受启动方式影响）
nonebot.init(_env_file=str(_BASE / ".env"))

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 加载 plugins/ 下的全部插件（绝对路径；模块名相对工作目录解析，故必须在 bot/ 下运行）
nonebot.load_plugins(str(_BASE / "plugins"))

if __name__ == "__main__":
    nonebot.run()
