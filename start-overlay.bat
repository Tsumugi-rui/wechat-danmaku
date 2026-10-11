@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 微信弹幕 - 桌面弹幕层

echo.
echo   ============================================================
echo     桌面弹幕层  ·  全屏透明 / 鼠标穿透 / 不影响正常用电脑
echo   ============================================================
echo.

rem ---- 优先使用项目自带的虚拟环境，其次才是系统 Python ----
set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul
  if not errorlevel 1 set "PY=.venv\Scripts\python.exe"
)
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   [错误] 没有找到 Python，请先安装 Python 3.9 或更高版本。
  echo.
  pause
  exit /b 1
)

!PY! -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo   [错误] 这个 Python 没有带 tkinter，无法绘制弹幕层。
  echo          请使用 python.org 官方版本。
  echo.
  pause
  exit /b 1
)

echo   启动中……设置取自 settings.json，面板里改完会自动生效。
echo   后台服务没在跑的话，会自动帮你拉起来。
echo.
echo   Ctrl+Alt+D  隐藏 / 显示弹幕
echo   Ctrl+Alt+Q  退出
echo   也可以直接在本窗口按 Ctrl+C 退出。
echo.

!PY! run_overlay.py %*

echo.
echo   弹幕层已退出。
pause
