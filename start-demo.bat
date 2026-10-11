@echo off
chcp 65001 >nul
cd /d "%~dp0"
title WeChat Danmaku - Demo

echo.
echo   Demo mode: playing simulated messages, WeChat is NOT required.
echo.

call start.bat --demo
