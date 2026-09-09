# Local development environment for the Matter-page refinement branch.
#
# Its own database and its own port, so it cannot collide with another
# session's runtime on this machine: two processes can bind the same loopback
# port on Windows and the older one keeps answering, which serves stale
# templates from a stale database.
#
# Dot-source it:  . devtools\matter-refinement\env.ps1

$env:PATH = "C:\Program Files\Git\cmd;C:\Program Files\Git\bin;$env:PATH"

$env:DJANGO_DEBUG = '1'
$env:DJANGO_SECRET_KEY = 'matter-refinement-local-only'
$env:DEV_LOGIN_ENABLED = '1'
$env:POSTGRES_DB = 'juristid_mpr'
$env:POSTGRES_USER = 'juristid'
$env:POSTGRES_PASSWORD = 'juristid'
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '5432'
$env:POSTGRES_SSLMODE = 'disable'
$env:PGPASSWORD = 'juristid'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1,[::1]'
$env:EVIDENCE_ROOT = 'C:\CC\juristid-matter-page-refinement\.devdata\evidence'

$global:MPR_PORT = 8078
$global:MPR_PGBIN = 'C:\CC\_pgsql18\pgsql\bin'
