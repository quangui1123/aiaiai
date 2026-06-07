@echo off
cd /d "D:\ai-token-proxy"
echo Starting AI Token Proxy...

REM Start the API server
start "AI-Proxy-Server" /MIN ".\python\python.exe" -m uvicorn server:app --host 0.0.0.0 --port 8000
echo API Server starting on port 8000...

REM Wait for server
timeout /t 3 /nobreak >nul

REM Start Cloudflare Tunnel
start "AI-Proxy-Tunnel" /MIN ".\tools\cloudflared.exe" tunnel --url http://localhost:8000
echo Cloudflare Tunnel starting...

timeout /t 5 /nobreak >nul
echo.
echo ============================================
echo   Server: http://localhost:8000
echo   Admin:  http://localhost:8000/admin
echo   Pricing: http://localhost:8000/pricing
echo   API Docs: http://localhost:8000/docs
echo.
echo   Check cf_log.txt for public URL
echo ============================================
echo.
echo Services running. Close this window to stop.
pause
