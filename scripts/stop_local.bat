@echo off
setlocal
cd /d "%~dp0.."

echo Stopping the RAG API window...
taskkill /FI "WINDOWTITLE eq RAG API" /T /F >nul 2>&1

echo Stopping Milvus and Attu containers...
docker compose -f infra\docker-compose.yml down

echo.
echo RAG services stopped. Docker Desktop remains available for other projects.
pause
