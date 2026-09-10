# One development server for this branch, on 8078. Development tool.
#
# Always kills whatever holds the port first: two processes can bind the same
# loopback port on Windows and the older one keeps answering, which serves
# stale templates from a stale database and looks exactly like a change that
# did not take.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
. "$root\devtools\matter-refinement\env.ps1"

$up = (Test-NetConnection 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue).TcpTestSucceeded
if (-not $up) {
    & "$MPR_PGBIN\pg_ctl.exe" -D 'C:\CC\_pgsql18\data' -l 'C:\CC\_pgsql18\server.log' `
        -o '-p 5432 -c listen_addresses=127.0.0.1' start | Out-Null
    Start-Sleep -Seconds 6
}

$holders = netstat -ano | Select-String "127.0.0.1:$MPR_PORT\s.*LISTENING" |
    ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
foreach ($holder in $holders) { try { Stop-Process -Id $holder -Force -ErrorAction Stop } catch {} }
if ($holders) { Start-Sleep -Seconds 2 }

Start-Process -FilePath 'uv' `
    -ArgumentList 'run', 'python', 'manage.py', 'runserver', "127.0.0.1:$MPR_PORT", '--noreload' `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput "$root\.devserver.log" -RedirectStandardError "$root\.devserver.err"
Start-Sleep -Seconds 8

$listeners = (netstat -ano | Select-String "127.0.0.1:$MPR_PORT\s.*LISTENING" | Measure-Object).Count
Write-Host "listeners on $MPR_PORT : $listeners"
try {
    (Invoke-WebRequest "http://127.0.0.1:$MPR_PORT/healthz" -UseBasicParsing).StatusCode
} catch {
    Write-Host "healthz failed"; Get-Content "$root\.devserver.err" -Tail 15
}
