@echo off
cd /d "%~dp0"

rem Force-close the widget when it will not react to the menu or Alt+Q.
rem Pure ASCII on purpose: cmd reads .bat byte by byte in the system code
rem page, and CJK comments get mangled into syntax-breaking characters.

echo Stopping widget...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'widget\.py' -and $_.Name -like 'python*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ('  stopped ' + @($p).Count + ' instance(s)') } else { Write-Host '  no running widget found' }"

timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$left = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'widget\.py' -and $_.Name -like 'python*' }).Count; if ($left -eq 0) { Write-Host 'OK: widget stopped.' } else { Write-Host ('WARN: ' + $left + ' instance(s) still running.') }"

timeout /t 4 /nobreak >nul
