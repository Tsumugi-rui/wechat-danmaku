@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 微信弹幕 - 推送到 GitHub

set "REMOTE=https://github.com/Tsumugi-rui/wechat-danmaku.git"

echo.
echo   ============================================================
echo     推送到 GitHub
echo     %REMOTE%
echo   ============================================================
echo.

rem ---------- 1. 找一个可用的 git ----------
set "GIT="
where git >nul 2>nul && set "GIT=git"
if not defined GIT (
  for %%P in (
    "D:\Workbuddy\resources\vendor\PortableGit\cmd\git.exe"
    "C:\Program Files\Git\cmd\git.exe"
    "%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
  ) do (
    if not defined GIT if exist %%P set "GIT=%%~P"
  )
)
if not defined GIT (
  echo   [错误] 没找到 git。请先安装 Git for Windows：https://git-scm.com/download/win
  echo.
  pause
  exit /b 1
)
echo   使用 git：%GIT%
echo.

rem ---------- 2. 确认本地已有提交 ----------
"%GIT%" rev-parse --verify HEAD >nul 2>nul
if errorlevel 1 (
  echo   [错误] 本地还没有任何提交，无法推送。
  echo.
  pause
  exit /b 1
)

rem ---------- 3. 设置远端 ----------
"%GIT%" remote remove origin >nul 2>nul
"%GIT%" remote add origin "%REMOTE%"
echo   远端已设置。
echo.

rem ---------- 4. 推送（证书问题自动降级重试）----------
echo   正在推送（首次会弹出 GitHub 登录窗口，在浏览器里点一下授权即可）...
echo.

"%GIT%" push -u origin main
if not errorlevel 1 goto OK

echo.
echo   [提示] 第一次推送失败，可能是本机网络代理改写了 TLS 证书。
echo          正在用兼容方式重试 ...
echo.

"%GIT%" -c http.schannelCheckRevoke=false push -u origin main
if not errorlevel 1 goto OK

echo.
echo   [提示] 再次失败，最后尝试关闭证书校验（仅本次推送有效，不改全局设置）...
echo.
"%GIT%" -c http.schannelCheckRevoke=false -c http.sslVerify=false push -u origin main
if not errorlevel 1 goto OK

echo.
echo   ============================================================
echo     [失败] 推送没有成功。常见原因：
echo       1) 远端仓库还没建 —— 先到 https://github.com/new 建一个
echo          名为 wechat-danmaku 的空仓库（不要勾 README / .gitignore）
echo       2) 登录窗口没弹出 —— 先在浏览器登录 GitHub，再重跑本脚本
echo       3) 仓库已存在但非空 —— 本脚本只做首次推送
echo   ============================================================
echo.
pause
exit /b 1

:OK
echo.
echo   ============================================================
echo     推送成功！仓库地址：
echo     https://github.com/Tsumugi-rui/wechat-danmaku
echo.
echo     新电脑上只需：
echo       git clone https://github.com/Tsumugi-rui/wechat-danmaku.git
echo       然后双击「首次安装.bat」重建环境
echo   ============================================================
echo.
pause
