@echo off
setlocal
cd /d "%~dp0"
echo [1/2] Tao moi truong Python...
py -3 -m venv .venv
if errorlevel 1 goto :error
echo [2/2] Hoan tat. Khong can tai them thu vien.
echo.
echo Hay bam START-SERVER.cmd, sau do tai thu muc extension vao Chrome.
pause
exit /b 0
:error
echo.
echo Cai dat that bai. Hay chup man hinh loi gui cho em.
pause
exit /b 1
