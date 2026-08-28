@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "gradlew.bat" (
  echo [ERROR] Gradle Wrapper is missing.
  exit /b 1
)
call gradlew.bat --no-daemon clean build
exit /b %ERRORLEVEL%
