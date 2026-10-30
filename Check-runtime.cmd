@echo off
setlocal
cd /d "%~dp0"
set "TCL_LIBRARY=%~dp0runtime\tcl\tcl8.6"
set "TK_LIBRARY=%~dp0runtime\tcl\tk8.6"
set "DATA=%~dp0data"
if exist "%~dp0installed-mode.txt" set "DATA=%LOCALAPPDATA%\PetDock"
if not exist "%DATA%" mkdir "%DATA%"
"%~dp0runtime\python.exe" "%~dp0app\runtime_check.py" > "%DATA%\runtime-check.txt" 2>&1
type "%DATA%\runtime-check.txt"
echo.
echo Results saved in "%DATA%\runtime-check.txt"
pause
endlocal
