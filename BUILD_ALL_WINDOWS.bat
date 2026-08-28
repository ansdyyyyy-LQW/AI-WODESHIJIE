@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

for /f "usebackq delims=" %%j in (`powershell -NoProfile -ExecutionPolicy Bypass -File tools\ensure_jdk17.ps1`) do set "PRIVATE_JDK=%%j"
if not defined PRIVATE_JDK (
  echo [ERROR] 无法准备 JDK 17。
  exit /b 1
)
set "JAVA_HOME=%PRIVATE_JDK%"
set "PATH=%JAVA_HOME%\bin;%PATH%"
java -version
if errorlevel 1 exit /b 1

set "PY=py -3.12"
%PY% --version >nul 2>&1 || set "PY=python"
%PY% --version >nul 2>&1 || (
  echo [ERROR] 未检测到 Python 3.12。正式用户不需要构建源码；开发构建请安装 Python 3.12。
  exit /b 1
)

pushd maid-ai-bridge
call BUILD_WINDOWS.bat
if errorlevel 1 (popd & exit /b 1)
popd

where node >nul 2>&1 || (
  echo [ERROR] 未检测到 Node.js。Windows 打包需要先准备锁定的 Node 运行环境。
  exit /b 1
)
where pnpm >nul 2>&1 || (
  echo [ERROR] 未检测到 pnpm。Windows 打包需要先安装锁定的 DSH 依赖。
  exit /b 1
)
call pnpm --dir dsh-integration install --frozen-lockfile --prod=false
if errorlevel 1 exit /b 1
call pnpm --dir dsh-integration run build
if errorlevel 1 exit /b 1
call pnpm --dir dsh-integration test
if errorlevel 1 exit /b 1

if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat
if not exist .test-tmp mkdir .test-tmp
set "TEMP=%CD%\.test-tmp"
set "TMP=%TEMP%"
python -m pip install --upgrade pip
python -m pip install -e agent-core[dev] -e control-center[test] -e rnd-runner[test]
python -m pytest agent-core/tests control-center/tests rnd-runner/tests -q
if errorlevel 1 exit /b 1

python tools\validate_source.py
if errorlevel 1 exit /b 1

echo [OK] Java、Python 与 DeepSeek Harness 构建/测试完成。
