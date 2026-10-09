@echo off
cd /d "%~dp0"

rem Python loads the source at startup, so a running widget keeps old code.
rem This kills the old instance and starts a fresh one.

echo Stopping old widget instance...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'widget\.py' -and $_.Name -like 'python*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ('  stopped ' + @($p).Count + ' instance(s)') } else { Write-Host '  no running widget found' }"

rem Wait for the single-instance lock to be released.
timeout /t 2 /nobreak >nul

echo Starting new widget...
call "%~dp0start-widget.bat"

timeout /t 2 /nobreak >nul
if exist widget-error.log (
  echo.
  echo --- widget-error.log tail ---
  powershell -NoProfile -Command "Get-Content -Tail 12 -Encoding UTF8 widget-error.log"
)
echo Done. If no window appears, press Alt+V, or check widget-error.log.
timeout /t 4 /nobreak >nul
