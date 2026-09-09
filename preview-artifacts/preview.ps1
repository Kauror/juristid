<#
.SYNOPSIS
  Start the Teema design-refinement preview locally. PREVIEW ONLY.

.DESCRIPTION
  One command from a clean machine to a browsable preview:

      pwsh preview-artifacts\preview.ps1 -Rebuild     # first time, or to reseed
      pwsh preview-artifacts\preview.ps1              # just start the server

  It uses its own database (juristid_mrp) and its own port (8077), so it cannot
  disturb another session's runtime. Two processes can bind the same loopback
  port on Windows and the older one keeps answering, which serves stale
  templates from a stale database — so this always kills whatever holds 8077
  before starting.

  Everything it creates is synthetic. It refuses to run against a configuration
  that claims to hold real data (app/core/management/commands, and the view's
  own gate in app/core/design_preview.py).
#>
[CmdletBinding()]
param(
    # Drop and rebuild the preview database, then reseed it. A Matter that has
    # history cannot be deleted — ChangeEvent is append-only and the foreign
    # keys are PROTECT — so reseeding means a fresh database.
    [switch]$Rebuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pgbin = 'C:\CC\_pgsql18\pgsql\bin'
$port = 8077

$env:DJANGO_DEBUG = '1'
$env:DJANGO_SECRET_KEY = 'preview-matter-refinement-not-a-secret'
$env:DEV_LOGIN_ENABLED = '1'
$env:POSTGRES_DB = 'juristid_mrp'
$env:POSTGRES_USER = 'juristid'
$env:POSTGRES_PASSWORD = 'juristid'
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '5432'
$env:POSTGRES_SSLMODE = 'disable'
$env:PGPASSWORD = 'juristid'
$env:EVIDENCE_ROOT = Join-Path $root '.preview-evidence'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1,[::1]'

# --- PostgreSQL ------------------------------------------------------------
$up = (Test-NetConnection 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue).TcpTestSucceeded
if (-not $up) {
    Write-Host 'starting PostgreSQL...'
    & "$pgbin\pg_ctl.exe" -D 'C:\CC\_pgsql18\data' -l 'C:\CC\_pgsql18\server.log' `
        -o '-p 5432 -c listen_addresses=127.0.0.1' start | Out-Null
    Start-Sleep -Seconds 6
}
& "$pgbin\pg_isready.exe" -h 127.0.0.1 -p 5432 -U juristid

# --- one server, never two -------------------------------------------------
# `$pid` is a read-only automatic variable in PowerShell; the loop variable
# cannot be called that.
$holders = netstat -ano | Select-String "127.0.0.1:$port\s.*LISTENING" |
    ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
foreach ($holder in $holders) { try { Stop-Process -Id $holder -Force -ErrorAction Stop } catch {} }
if ($holders) { Start-Sleep -Seconds 2 }

# --- database --------------------------------------------------------------
if ($Rebuild) {
    Write-Host 'rebuilding the preview database...'
    & "$pgbin\dropdb.exe" -h 127.0.0.1 -U juristid --force --if-exists juristid_mrp
    & "$pgbin\createdb.exe" -h 127.0.0.1 -U juristid -T template0 `
        --locale-provider=icu --icu-locale=en-US -E UTF8 juristid_mrp
    uv run python manage.py migrate --noinput | Out-Null
    uv run python manage.py seed_e2e_data | Out-Null
}

$matter = (uv run python manage.py seed_matter_refinement_preview | Out-String).Trim()

# --- serve -----------------------------------------------------------------
Start-Process -FilePath 'uv' `
    -ArgumentList 'run', 'python', 'manage.py', 'runserver', "127.0.0.1:$port", '--noreload' `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root '.preview-server.log') `
    -RedirectStandardError (Join-Path $root '.preview-server.err')
Start-Sleep -Seconds 8

Write-Host ''
Write-Host 'Teema refinement preview is up.'
Write-Host ''
Write-Host "  Sign in     http://127.0.0.1:$port/konto/arendus-sisselogimine/   (Mari Naidisjurist)"
Write-Host "  Current     http://127.0.0.1:$port/teemad/$matter/"
Write-Host "  Preview     http://127.0.0.1:$port/disainisusteem/teema-refinement/$matter/"
Write-Host ''
