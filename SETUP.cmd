@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] Tạo môi trường Python local...
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  where python >nul 2>&1
  if errorlevel 1 goto :python_missing
  python -m venv .venv
)
if errorlevel 1 goto :error

echo [2/2] Hoàn tất. Không cần tải thêm thư viện.
echo.
echo Hãy chạy START-SERVER.cmd, sau đó nạp thư mục extension vào Chrome hoặc Cốc Cốc.
pause
exit /b 0

:python_missing
echo.
echo Không tìm thấy Python 3 trên máy.
echo Hãy cài Python 3 và bật tùy chọn Add Python to PATH.
pause
exit /b 1

:error
echo.
echo Tạo môi trường Python thất bại. Hãy chụp màn hình lỗi để kiểm tra.
pause
exit /b 1
