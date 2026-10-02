@echo off
rem 启动 NoneBot 机器人（双击运行；保持窗口开着，CTRL+C 停止）
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo [错误] 未找到 .venv，先执行:
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
.venv\Scripts\python.exe bot.py
pause
