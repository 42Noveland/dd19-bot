@echo off
setlocal
title dd19 机器人
cd /d %~dp0

rem ================================================
rem  dd19 QQ 机器人 一键启动（双击本文件运行）
rem  保持本窗口开着；CTRL+C 或关闭窗口 = 停止机器人
rem ================================================

rem --- 1) 检测端口占用：机器人已在运行时的处理 ---
netstat -ano | findstr /C:"127.0.0.1:8081" | findstr "LISTENING" >nul 2>nul
if %errorlevel%==0 (
  echo [提示] 检测到端口 8081 被占用：机器人可能已经在运行。
  echo        误启动的话，直接关掉本窗口即可。
  echo        要重启的话：按任意键 = 结束旧实例并启动新的。
  pause >nul
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:"127.0.0.1:8081" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>nul
  ping 127.0.0.1 -n 2 >nul
  echo [OK] 旧实例已结束，启动新实例...
)

rem --- 2) 虚拟环境检查 ---
if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv 虚拟环境，请先执行：
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

rem --- 3) 启动 ---
echo [启动] bot.py 运行中（本窗口保持开着，CTRL+C 停止）...
.venv\Scripts\python.exe bot.py

echo.
echo [已退出] 机器人进程结束了，上面是最后的日志。
pause >nul
