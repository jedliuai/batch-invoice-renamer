@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 (
    echo 请先安装 Python 3.10 或以上版本，并勾选 Add Python to PATH。
    pause
    exit /b 1
)
if not exist "%~dp0.venv\Scripts\python.exe" (
    python -m venv "%~dp0.venv"
    if errorlevel 1 goto failed
)
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
echo.
echo 安装完成！以后直接双击「一键重命名.bat」即可。
pause
exit /b 0
:failed
echo.
echo 安装失败，请检查上方提示和网络连接后重试。
pause
exit /b 1
