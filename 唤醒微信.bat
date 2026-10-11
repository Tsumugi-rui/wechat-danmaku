@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 微信弹幕 - 唤醒微信无障碍

echo.
echo   ============================================================
echo     唤醒微信无障碍树  ·  让弹幕读得到微信消息
echo   ============================================================
echo.
echo   微信 4.1+ 是自绘界面，默认不向系统暴露控件树，
echo   所以任何自动化库都连不上、只能看到一层 Qt 空壳。
echo   本工具会临时把这棵树打开。
echo.
echo   做法：置系统「存在屏幕阅读器」标志
echo         + 向微信进程写入 1 个状态字节（就是 Qt 自己也会写的那个位）
echo         + 写完立刻用控件树校验，不生效会自动回滚。
echo.
echo   不注入代码、不重启微信、不改微信文件。
echo   ^>^> 微信重启后需要重新唤醒一次。^<^<
echo.

rem ---- 必须用装了 wxauto4 的解释器，否则无法校验激活是否生效 ----
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
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

!PY! -c "import wxauto4" >nul 2>nul
if errorlevel 1 (
  echo   [错误] 当前解释器里没有 wxauto4，无法校验激活结果。
  echo          请用项目自带的 .venv 运行本脚本。
  echo.
  pause
  exit /b 1
)

echo   ---- 激活前状态 ----
echo.
!PY! tools/wechat_uia_wake.py status
echo.
echo   ---- 开始唤醒 ----
echo.
!PY! tools/wechat_uia_wake.py wake

if errorlevel 1 (
  echo.
  echo   [提示] 唤醒没成功。请确认：
  echo          1. 微信已经启动并登录；
  echo          2. 微信主窗口没有最小化；
  echo          3. 微信没有以管理员身份运行（若是，本脚本也要用管理员权限跑）。
)

echo.
pause
