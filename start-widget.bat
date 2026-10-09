@echo off
cd /d "%~dp0"

rem The widget needs tkinter; the managed Python 3.13 build has none.
rem Probe known interpreter locations first, then fall back to PATH.
set "PY="
for %%P in (
  "C:\Users\admin\AppData\Local\Programs\Python\Python310\python.exe"
  "C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"
  "C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe"
  "C:\Users\admin\AppData\Local\Programs\Python\Python39\python.exe"
) do if not defined PY if exist %%P set "PY=%%~P"

if not defined PY (
  for %%P in (python.exe py.exe) do (
    if not defined PY where %%P >nul 2>nul && set "PY=%%P"
  )
)

if not defined PY (
  echo [ERROR] No usable Python found.
  echo Install Python 3.8+ with the "tcl/tk and IDLE" component enabled.
  pause
  exit /b 1
)

rem pythonw.exe has no console. python.exe's console is the same process:
rem closing that black window kills the widget.
for %%I in ("%PY%") do if exist "%%~dpIpythonw.exe" set "PY=%%~dpIpythonw.exe"
start "" "%PY%" widget.py
exit /b 0
