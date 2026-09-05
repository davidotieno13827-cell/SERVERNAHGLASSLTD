@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo POS environment not found. Run the setup commands in README.md first.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$ErrorActionPreference='Stop'; $url='http://127.0.0.1:5000'; $log=(Join-Path (Get-Location) 'instance\server.log'); $errorLog=(Join-Path (Get-Location) 'instance\server-error.log'); $ready=$false; try { $response=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1; $ready=$response.StatusCode -eq 200 } catch {}; if (-not $ready) { Start-Process -FilePath '.venv\Scripts\pythonw.exe' -ArgumentList 'run.py' -WorkingDirectory (Get-Location) -RedirectStandardOutput $log -RedirectStandardError $errorLog -WindowStyle Hidden; foreach ($attempt in 1..50) { Start-Sleep -Milliseconds 100; try { $response=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1; if ($response.StatusCode -eq 200) { $ready=$true; break } } catch {} } }; if ($ready) { Start-Process $url } else { Add-Content -Path $errorLog -Value ('Startup check failed at ' + (Get-Date -Format s)); exit 2 }"