@echo off
title Star Math RAG - Launcher
cd /d "%~dp0"

echo ============================================
echo    Star Math RAG System - Start
echo ============================================
echo.

netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 goto start_backend
echo [1/2] Backend already running (8000).
goto open_browser

:start_backend
echo [1/2] Starting backend (uvicorn :8000)...
start "rag-backend" /min /D "%~dp0backend" ".venv\Scripts\python.exe" run.py
call :waitport 8000
echo        backend ready.
echo.

:open_browser
echo [2/2] Opening browser...
start "" "http://127.0.0.1:8000"
echo.
echo All done! Default account: admin / admin123
echo Backend runs in a minimized window. To stop, double-click stop.bat.
echo LLM: DeepSeek (chat/solve) + Qwen-VL-OCR (image) - no local gateway needed.
echo.
pause
exit /b

:waitport
set _port=%1
set _n=0
:waitloop
ping -n 2 127.0.0.1 >nul
netstat -ano | findstr ":%_port% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 exit /b
set /a _n+=1
if %_n% geq 30 goto waitfail
goto waitloop
:waitfail
echo        WARN: port %_port% timeout. Check service window or logs.
exit /b 1
