@echo off
title RAG 一键启动
cd /d "%~dp0"

echo ============================================
echo   RAG 项目一键启动
echo   时间: %date% %time%
echo ============================================
echo.

echo [1/4] 启动基础设施 Milvus / Redis / MySQL ...
docker compose --env-file .env -f infra/docker-compose.yml up -d
if errorlevel 1 (
    echo [!] 启动失败。请确认 Docker Desktop 已打开，且根目录 .env 已配置。
    pause
    exit /b 1
)

echo [*] 等待 Milvus 就绪（约 2.5 分钟）...
set /a tries=0
:waitmilvus
timeout /t 5 /nobreak >nul
curl -s -o nul http://127.0.0.1:9091/healthz && goto milvus_ok
set /a tries+=1
if %tries% lss 30 goto waitmilvus
echo [!] Milvus 等待超时，基础设施可能未完全就绪。
:milvus_ok
echo [OK] 基础设施已就绪。
echo.

echo [2/4] 启动 API ...
netstat -ano | findstr "LISTENING" | findstr ":8504 " >nul && (
    echo [*] API 已在运行，跳过。
) || (
    start "RAG API" cmd /k "D:\mnist_data\ancanda\envs\rag11\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8504"
    echo [OK] API 窗口已打开。
)

echo [*] 等待 API 就绪（首次启动约 30~60 秒，加载中文 Embedding 模型）...
set /a atries=0
:waitapi
timeout /t 3 /nobreak >nul
curl -s -o nul http://127.0.0.1:8504/api/v1/health && goto api_ok
set /a atries+=1
if %atries% lss 40 goto waitapi
echo [!] API 等待超时，请检查 RAG API 窗口里的报错。
:api_ok
echo [OK] API 已就绪。
echo.

echo [3/4] 启动 Worker ...
start "RAG Worker" cmd /k "D:\mnist_data\ancanda\envs\rag11\python.exe -m backend.app.tasks.worker"
echo [OK] Worker 窗口已打开。
echo.

echo [4/4] 启动前端 ...
netstat -ano | findstr "LISTENING" | findstr ":5174 " >nul && (
    echo [*] 前端已在运行，跳过。
) || (
    start "RAG Frontend" cmd /k "cd /d frontend && npm run dev"
    echo [OK] 前端窗口已打开。
)
echo.

echo ============================================
echo   全部启动完成
echo   前端    http://127.0.0.1:5174
echo   API 文档 http://127.0.0.1:8504/docs
echo   健康检查 curl http://127.0.0.1:8504/api/v1/health
echo ============================================
echo.
echo 每个服务都在自己的窗口里，关闭对应窗口即停止该服务。
echo 本窗口现在可以关闭。
pause
