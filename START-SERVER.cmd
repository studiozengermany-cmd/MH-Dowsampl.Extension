@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title MH-Dowsample Server

echo ========================================
echo   MH-Dowsample Server 1.3.0
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Lần chạy đầu: đang chuẩn bị Python...
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    where python >nul 2>&1
    if errorlevel 1 goto :python_missing
    python -m venv .venv
  )
  if errorlevel 1 goto :setup_error
  echo [2/2] Đã chuẩn bị xong.
  echo.
)

".venv\Scripts\python.exe" backend\server.py
set "SERVER_EXIT=%ERRORLEVEL%"
echo.
if "%SERVER_EXIT%"=="0" (
  echo Server đã dừng.
) else (
  echo Server không thể khởi động hoặc đã dừng vì lỗi.
  echo Nếu cổng 8765 đã có server chạy, chỉ cần dùng cửa sổ đang chạy đó.
)
echo.
pause
exit /b %SERVER_EXIT%

:python_missing
echo Không tìm thấy Python 3 trên máy.
echo Hãy cài Python 3.12 trở lên và bật tùy chọn Add Python to PATH.
pause
exit /b 1

:setup_error
echo Không tạo được môi trường Python local.
echo Hãy kiểm tra quyền ghi trong thư mục dự án rồi chạy lại file này.
pause
exit /b 1
