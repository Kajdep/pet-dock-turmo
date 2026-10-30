@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime\python.exe" (
  echo Run this from the extracted Pet Dock folder beside Start.cmd.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" "%~dp0app\restore_pet.py"
pause
endlocal
