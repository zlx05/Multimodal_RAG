@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."

echo [1/4] Checking Docker Desktop...
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker engine is not running. Starting Docker Desktop...
  docker desktop start
)

set /a attempts=0
:wait_docker
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a attempts+=1
if !attempts! geq 60 (
  echo Docker engine did not become ready within 3 minutes.
  pause
  exit /b 1
)
timeout /t 3 /nobreak >nul
goto wait_docker

:docker_ready
echo [2/4] Starting infra services (Milvus, Redis, MySQL)...
docker compose --env-file .env -f infra\docker-compose.yml up -d
if errorlevel 1 (
  echo Failed to start infra services. Check root .env has MYSQL_ROOT_PASSWORD and MYSQL_PASSWORD.
  pause
  exit /b 1
)

echo [3/4] Starting RAG API web app...
if not defined PYTHON_EXE set "PYTHON_EXE=python"
"%PYTHON_EXE%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo Cannot find a usable Python interpreter. Set PYTHON_EXE first.
  pause
  exit /b 1
)
start "RAG API" /D "%~dp0.." "%PYTHON_EXE%" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8504

timeout /t 5 /nobreak >nul
echo [4/4] Opening the pages...
start "" http://127.0.0.1:8504
start "" http://127.0.0.1:8000
echo.
echo RAG API:  http://127.0.0.1:8504
echo Attu:     http://127.0.0.1:8000
echo Keep this window if you want to see the startup result.
pause
