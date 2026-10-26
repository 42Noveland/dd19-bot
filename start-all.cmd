@echo off
setlocal enabledelayedexpansion
title dd19 QQ 机器人 - 一键启动
cd /d %~dp0

rem ================================================
rem  dd19 QQ 机器人 一键启动（双击本文件运行）
rem  顺序：先启动 NapCat（协议端）-> 等它就绪 -> 再启动 bot
rem  NapCat 和 bot 各占一个窗口，两个窗口都保持开着
rem ================================================

set "NAPCAT_DIR=%~dp0napcat\NapCat.Shell.Node"
set "BOT_DIR=%~dp0bot"

echo ================================================
echo  dd19 QQ 机器人 - 一键启动
echo ================================================
echo.

rem --- 1) NapCat：已在运行则跳过 ---
netstat -ano | findstr /C:"127.0.0.1:6099" | findstr "LISTENING" >nul 2>nul
if %errorlevel%==0 goto napcat_already

if not exist "%NAPCAT_DIR%\node.exe" (
  echo [错误] 未找到 NapCat：%NAPCAT_DIR%\node.exe
  echo        请确认 napcat\NapCat.Shell.Node 目录完整。
  pause
  exit /b 1
)

echo [启动] 正在启动 NapCat（协议端）...
start "NapCat" /D "%NAPCAT_DIR%" cmd /c napcat.bat

rem 等 6099 端口就绪（最多约 30 秒；每轮 2 秒）
set /a _wait=0
:wait_napcat
ping 127.0.0.1 -n 2 >nul
set /a _wait+=1
netstat -ano | findstr /C:"127.0.0.1:6099" | findstr "LISTENING" >nul 2>nul
if %errorlevel%==0 goto napcat_ready
if !_wait! lss 15 goto wait_napcat
echo [警告] 30 秒内没等到 NapCat 的 6099 端口，请看一眼 NapCat 窗口。
echo        仍然继续启动 bot（bot 会自动重连，NapCat 就绪后即可用）。
goto napcat_done

:napcat_ready
echo [OK] NapCat 已就绪（约 !_wait! 秒）。若 NapCat 窗口提示扫码，请先登录。
goto napcat_done

:napcat_already
echo [提示] NapCat 已在运行（6099 端口监听），跳过启动。

:napcat_done
echo.

rem --- 2) bot：已在运行则跳过 ---
netstat -ano | findstr /C:"127.0.0.1:8081" | findstr "LISTENING" >nul 2>nul
if %errorlevel%==0 goto bot_already

if not exist "%BOT_DIR%\start-bot.cmd" (
  echo [错误] 未找到 bot 启动脚本：%BOT_DIR%\start-bot.cmd
  pause
  exit /b 1
)

echo [启动] 正在启动 bot...
start "dd19 bot" /D "%BOT_DIR%" cmd /c start-bot.cmd
echo [OK] bot 已启动（看它自己的窗口：启动报告 + 连接状态）。
goto all_done

:bot_already
echo [提示] bot 已在运行（8081 端口监听），跳过启动。
echo        要重启的话：先关掉旧的 bot 窗口，再重新运行本脚本。

:all_done
echo.
echo [完成] 启动流程结束。NapCat / bot 各占一个窗口，关掉对应窗口 = 停止对应进程。
echo        本窗口可以关闭（按任意键）。
pause >nul
