@echo off
title Star Math RAG - Smoke Test
cd /d "%~dp0"
echo ============================================
echo   Star Math RAG System - Smoke Test
echo ============================================
echo.

netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Backend is NOT running on 8000. Run start.bat first.
    pause
    exit /b 1
)

echo Running smoke tests (backend on 8000)...
"%~dp0backend\.venv\Scripts\python.exe" "%~dp0backend\scripts\smoke_test.py" --base http://127.0.0.1:8000
set EC=%ERRORLEVEL%
echo.
if %EC%==0 (
    echo All smoke checks passed.
) else (
    echo Some checks FAILED. See output above.
)
pause
exit /b %EC%
