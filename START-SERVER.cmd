@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Chua cai dat. Hay bam SETUP.cmd truoc.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" backend\server.py
pause
