@echo off
title Stop RAG Services
setlocal
set "PIDF=%~dp0backend\storage\tunnel.pid"
echo Stopping services (backend 8000 / LLM gateway 4096 / tunnel)...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000,4096 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "Get-Process -Name cloudflared,cpolar -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"
powershell -NoProfile -Command "$f='%PIDF%'; if(-not (Test-Path -LiteralPath $f)){ return }; $raw=[string](Get-Content -LiteralPath $f -TotalCount 1); $tpid=0; if([int]::TryParse($raw.Trim(),[ref]$tpid) -and $tpid -gt 4){ $pr=Get-Process -Id $tpid -ErrorAction SilentlyContinue; if($pr -and (@('ssh','cloudflared','cpolar') -contains $pr.ProcessName)){ Stop-Process -Id $tpid -Force -ErrorAction SilentlyContinue; Write-Host ('stopped tunnel pid ' + $tpid) } }; Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue"
echo Done. If nothing stopped, services were already down.
pause
