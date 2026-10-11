@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title WeChat Danmaku

echo.
echo   ============================================
echo     WeChat Danmaku  -  starting service
echo   ============================================
echo.

rem ---- 优先使用项目自带的虚拟环境（微信监听库装在里面）----
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  echo   [ERROR] Python not found.
  echo   Please install Python 3.9+ from https://www.python.org/downloads/
  echo   and make sure "Add Python to PATH" is checked.
  echo.
  pause
  exit /b 1
)

echo   Using Python: !PY!
echo.

!PY! app.py %*

echo.
echo   Service stopped.
pause
