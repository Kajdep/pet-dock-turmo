@echo off
setlocal
cd /d "%~dp0"
set "TCL_LIBRARY=%~dp0runtime\tcl\tcl8.6"
set "TK_LIBRARY=%~dp0runtime\tcl\tk8.6"
if not exist data mkdir data
"%~dp0runtime\python.exe" "%~dp0app\runtime_check.py" > "%~dp0data\runtime-check.txt" 2>&1
type "%~dp0data\runtime-check.txt"
echo.
echo Results saved in data\runtime-check.txt
pause
endlocal
