@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title A-share realtime board

rem monitor.py prints Chinese to stdout as UTF-8, hence chcp 65001 above.
rem This file itself stays pure ASCII so cmd never mis-parses it.
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "C:\Users\admin\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\admin\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY if exist "C:\Users\admin\AppData\Local\Programs\Python\Python310\python.exe" set "PY=C:\Users\admin\AppData\Local\Programs\Python\Python310\python.exe"

if not defined PY (
  echo [ERROR] No Python found. Install Python 3.8+ first.
  pause
  exit /b 1
)

echo Starting board... the browser opens automatically.
echo Close this window to stop monitoring.
echo.
%PY% "%~dp0..\app\monitor.py"
echo.
echo Board stopped.
pause
