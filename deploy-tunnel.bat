@echo off
setlocal
title Star Math - Public Demo
cd /d "%~dp0"

set "PORT=8000"
set "LOGB=%~dp0backend\storage\tunnel"
set "LOGR=backend\storage\tunnel.out"
set "PIDF=%~dp0backend\storage\tunnel.pid"
set "TUNDIR=%~dp0tools"

echo ================================================================
echo    Star Math - temporary public demo launcher
echo ================================================================
echo.

REM ---------- 1) backend ----------
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto be_ready
echo [1/4] Starting backend on port %PORT% ...
start "rag-backend" /min /D "%~dp0backend" ".venv\Scripts\python.exe" run.py
set /a _w=0
:waitbe
ping -n 2 127.0.0.1 >nul
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto be_ok
set /a _w+=1
if %_w% lss 40 goto waitbe
echo       Backend did not start in time. Run start.bat first to debug.
goto end
:be_ok
echo       Backend ready.
goto tun_find
:be_ready
echo [1/4] Backend already running on port %PORT%.
goto tun_find

REM ---------- 2) pick tunnel method ----------
:tun_find
echo.
echo [2/4] Choosing tunnel method ...
set "KIND="
set "TUN="
if exist "%PIDF%" del /q "%PIDF%" >nul 2>&1

if exist "%TUNDIR%\cpolar.exe" call :pick_cpolar "%TUNDIR%\cpolar.exe"
if defined KIND goto tun_summary
if exist "%ProgramFiles%\cpolar\cpolar.exe" call :pick_cpolar "%ProgramFiles%\cpolar\cpolar.exe"
if defined KIND goto tun_summary
if exist "%TUNDIR%\cloudflared.exe" call :pick_cf "%TUNDIR%\cloudflared.exe"
if defined KIND goto tun_summary
for %%X in (cloudflared.exe) do if not "%%~$PATH:X"=="" call :pick_cf "%%~$PATH:X"
if defined KIND goto tun_summary
for %%X in (cpolar.exe) do if not "%%~$PATH:X"=="" call :pick_cpolar "%%~$PATH:X"
if defined KIND goto tun_summary

for %%X in (ssh.exe) do if "%%~$PATH:X"=="" goto no_ssh
set "KIND=ssh"

:tun_summary
echo       method = %KIND%
if /I not "%KIND%"=="ssh" goto start_tunnel
echo       No tunnel client installed, so using the SSH tunnel built into
echo       Windows (serveo.net). Nothing to download, no account needed.
goto start_tunnel

:no_ssh
echo       ssh.exe was not found and no cloudflared/cpolar is installed.
echo.
echo   Fix with either:
echo     - Settings ^> Apps ^> Optional features ^> Add a feature
echo       ^> OpenSSH Client      (built into Windows, free)
echo     - install cpolar, fastest in China: https://www.cpolar.com
goto end

REM ---------- 3) start tunnel ----------
REM cmd.exe owns the output file directly. Redirecting through PowerShell
REM pipes does not work here: the launcher PowerShell exits right away and
REM the pipe reader disappears, leaving an empty log.
:start_tunnel
echo.
echo [3/4] Starting tunnel ...
powershell -NoProfile -Command "Get-Process -Name cloudflared,cpolar -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"
if exist "%LOGR%" del /q "%LOGR%" >nul 2>&1
if /I "%KIND%"=="ssh" goto run_ssh
for %%A in ("%TUN%") do set "TUNS=%%~sA"
if /I "%KIND%"=="cpolar" goto run_cpolar
start "starmath-tunnel" /min cmd /c "%TUNS% tunnel --url http://127.0.0.1:%PORT% --no-autoupdate > %LOGR% 2>&1"
goto record_pid
:run_cpolar
start "starmath-tunnel" /min cmd /c "%TUNS% http %PORT% > %LOGR% 2>&1"
goto record_pid
:run_ssh
REM 127.0.0.1 on purpose: "localhost" may resolve to IPv6 ::1 while uvicorn
REM listens on IPv4 only, which makes the relay answer 502 Bad Gateway.
start "starmath-tunnel" /min cmd /c "ssh.exe -T -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:%PORT% serveo.net > %LOGR% 2>&1"

REM ---------- extract public URL ----------
:record_pid
if /I not "%KIND%"=="ssh" goto wait_url
powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ssh.exe' -and $_.CommandLine -like '*serveo.net*' } | Select-Object -First 1; if($p){ $p.ProcessId }" > "%PIDF%"

:wait_url
set /a _w=0
:url_loop
set "URL="
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "$f='%LOGB%.out','%LOGB%.err'; foreach($p in $f){ if(Test-Path -LiteralPath $p){ try{ $t=[string](Get-Content -Raw -LiteralPath $p) }catch{ $t='' } $a=[regex]::Match($t,'https://[a-z0-9.-]+\.serveousercontent\.com'); if($a.Success){ $a.Value; return } $b=[regex]::Match($t,'https://[a-z0-9.-]*trycloudflare\.com'); if($b.Success){ $b.Value; return } $c=[regex]::Match($t,'https://[a-z0-9.-]*cpolar\.[a-z]+'); if($c.Success){ $c.Value; return } } }"`) do set "URL=%%U"
if defined URL goto got_url
set /a _w+=1
if %_w% geq 25 goto url_fail
ping -n 2 127.0.0.1 >nul
goto url_loop

REM ---------- 4) verify from the public side ----------
:got_url
echo.
echo [4/4] Verifying the public URL works ...
set "CODE="
set /a _v=0
:verify_loop
set /a _v+=1
for /f "usebackq delims=" %%C in (`powershell -NoProfile -Command "try{ $r=Invoke-WebRequest -UseBasicParsing -Uri '%URL%/api/health' -TimeoutSec 20; $r.StatusCode }catch{ 0 }"`) do set "CODE=%%C"
if "%CODE%"=="200" goto verify_ok
if %_v% geq 4 goto verify_bad
ping -n 4 127.0.0.1 >nul
goto verify_loop
:verify_ok
echo       Public check passed (HTTP 200).
goto banner
:verify_bad
echo       Public check returned %CODE%. The relay may still be warming up.
echo       Wait about 30 seconds and refresh the URL in a browser.

:banner
echo.
echo ================================================================
echo   PUBLIC URL - send this to your teacher:
echo.
echo   %URL%
echo ----------------------------------------------------------------
echo   Local        http://127.0.0.1:%PORT%
echo   Admin user   admin
echo   Admin pass   see DEFAULT_ADMIN_PASSWORD in backend\.env
echo   Invite code  starmath-2026
echo ----------------------------------------------------------------
echo   Reminders
echo   - Keep this PC powered on, awake and online
echo   - The URL changes every time the tunnel restarts; rerun this script
echo   - Open it once on 4G mobile data to confirm real public access
echo   - To stop everything: run stop.bat
echo ================================================================
goto end

:url_fail
echo.
echo       Could not detect a public URL. Tunnel log tail:
echo ----------------------------------------------------------------
powershell -NoProfile -Command "Get-Content -Path '%LOGB%.out','%LOGB%.err' -Tail 15 -ErrorAction SilentlyContinue"
echo ----------------------------------------------------------------
echo   ssh + serveo may be blocked here. Alternatives:
echo     - cpolar (fast in China, free account): https://www.cpolar.com
echo     - cloudflared, but github.com downloads are very slow locally
goto end

REM ---------- helpers ----------
:pick_cf
set "CHK="
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$p='%~1'; if((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p).Length -gt 30000000)){ 'OK' }"`) do set "CHK=%%V"
if not "%CHK%"=="OK" echo       skip unusable cloudflared (incomplete?): %~1
if not "%CHK%"=="OK" exit /b
set "TUN=%~1"
set "KIND=cloudflared"
exit /b

:pick_cpolar
set "CHK="
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$p='%~1'; if((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p).Length -gt 1000000)){ 'OK' }"`) do set "CHK=%%V"
if not "%CHK%"=="OK" echo       skip unusable cpolar (incomplete?): %~1
if not "%CHK%"=="OK" exit /b
set "TUN=%~1"
set "KIND=cpolar"
exit /b

:end
echo.
echo Closing this window does NOT stop the services.
pause
exit /b