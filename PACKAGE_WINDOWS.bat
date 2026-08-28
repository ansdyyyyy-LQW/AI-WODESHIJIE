@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
call BUILD_ALL_WINDOWS.bat
if errorlevel 1 exit /b 1

call .venv\Scripts\activate.bat
python -m pip install pyinstaller PySide6 keyring
python tools\package_windows.py
if errorlevel 1 exit /b 1

echo [OK] 交付目录：dist\MaidAI
echo [OK] 压缩包：dist\MaidAI-Windows-0.3.0.zip
