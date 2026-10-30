@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime\pythonw.exe" (
  echo The portable runtime is missing.
  echo Extract the ENTIRE ZIP before running Start.cmd.
  pause
  exit /b 1
)
if not exist "%~dp0app\bootstrap.py" (
  echo The app folder is missing. Extract the entire ZIP again.
  pause
  exit /b 1
)
set "TCL_LIBRARY=%~dp0runtime\tcl\tcl8.6"
set "TK_LIBRARY=%~dp0runtime\tcl\tk8.6"
start "" "%~dp0runtime\pythonw.exe" "%~dp0app\bootstrap.py"
endlocal
