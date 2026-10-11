@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 微信弹幕 - 首次安装（只需运行一次）

echo.
echo   ============================================================
echo     首次安装  ·  为新电脑准备独立运行环境
echo   ============================================================
echo.

rem ---- 1. 找一个可用的 Python（必须自带 tkinter）----
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   [错误] 没找到 Python。请先到 python.org 安装 Python 3.9 或更高版本，
  echo          安装时务必勾选 "tcl/tk and IDLE"（本程序要用 tkinter 画弹幕）。
  echo.
  pause
  exit /b 1
)

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo   [错误] 这个 Python 没有带 tkinter，无法绘制弹幕层。
  echo          请改用 python.org 官方安装版，并在安装时勾选 tcl/tk。
  echo.
  pause
  exit /b 1
)

rem ---- 2. 建独立环境（.venv 不随项目迁移，必须在新机器重建）----
echo   [1/3] 创建独立运行环境 .venv ...
if exist ".venv\Scripts\python.exe" (
  echo         已经存在，跳过。
) else (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   [错误] 创建 .venv 失败。
    echo.
    pause
    exit /b 1
  )
)

echo   [2/3] 升级 pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul

echo   [3/3] 安装微信监听依赖（wxauto4，约 10~20 MB）...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo   [提示] 依赖安装没成功。若是网络问题，可换国内镜像重试：
  echo          ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================================
echo     完成！接下来：
echo       · 双击「设置面板.bat」   调设置、看开关
echo       · 双击「start-overlay.bat」 启动桌面弹幕层
echo       · 微信 4.x 用户：先双击「唤醒微信.bat」再启动弹幕层
echo   ============================================================
echo.
pause
