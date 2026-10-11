@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 微信弹幕 - 设置面板

echo.
echo   正在打开设置面板……
echo.

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

!PY! desktop/settings_gui.py

if errorlevel 1 (
  echo.
  echo   [提示] 面板异常退出，上面的信息可能有用。
  pause
)
