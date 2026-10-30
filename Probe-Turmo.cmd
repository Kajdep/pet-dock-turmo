@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime\python.exe" (
  echo Extract this update into your existing Pet Dock folder beside Start.cmd.
  pause
  exit /b 1
)
echo TURMO device diagnostic
 echo.
echo I - Inspect USB identity and driver details only. Does not write to LCD.
echo B - Benchmark known Protocol A image transfers. Close Pet Dock and Formbig first.
echo Q - Quit.
echo.
choice /c IBQ /n /m "Choose I, B, or Q: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto benchmark
"%~dp0runtime\python.exe" "%~dp0app\device_probe.py"
goto done
:benchmark
"%~dp0runtime\python.exe" "%~dp0app\device_probe.py" --benchmark --revision A --orientation Portrait
:done
echo.
set "DATA=%~dp0data"
if exist "%~dp0installed-mode.txt" set "DATA=%LOCALAPPDATA%\PetDock"
echo Results: "%DATA%\turmo-device-report.json"
pause
endlocal
