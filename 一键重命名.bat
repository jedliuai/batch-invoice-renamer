@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "INVOICE_PYTHON=%~dp0.venv\Scripts\python.exe"
    set "INVOICE_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
) else (
    set "INVOICE_PYTHON=python"
    set "INVOICE_PYTHONW=pythonw"
)
"%INVOICE_PYTHON%" -c "import tkinter, pdfplumber" >nul 2>&1
if errorlevel 1 (
    echo 尚未安装 Python 或所需依赖。
    echo 请先安装 Python 3.10 或以上版本，并勾选 Add Python to PATH。
    echo 然后双击本目录中的「安装依赖.bat」。
    pause
    exit /b 1
)
start "" "%INVOICE_PYTHONW%" "%~dp0rename_invoice.py"
exit /b 0
