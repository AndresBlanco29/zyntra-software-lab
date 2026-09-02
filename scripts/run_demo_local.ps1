# Zyntra Software Lab — local DEMO on isolated SQLite (never LTG MySQL).
param(
    [switch]$SkipSeed,
    [switch]$FreshDb,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$AppRoot = Split-Path -Parent $PSScriptRoot
$SqlitePath = Join-Path $AppRoot "config\db_demo.sqlite3"
$ManagePy = Join-Path $AppRoot "manage.py"

if (-not (Test-Path $ManagePy)) {
    Write-Error "manage.py not found under $AppRoot"
}

Set-Location $AppRoot

# Process env wins over .env for isolation-critical keys (see settings.py).
$env:DEMO_MODE = "1"
$env:SHOWCASE_MODE = "1"
$env:DEMO_USE_SQLITE = "1"
$env:DEMO_ENVIRONMENT_LABEL = "SOFTWARE LAB"
$env:DEMO_BRAND_NAME = "Zyntra"
$env:QUICKBOOKS_PROVIDER = "mock"
$env:QUICKBOOKS_ENVIRONMENT = "sandbox"
$env:DEMO_DISABLE_OUTBOUND_EMAIL = "1"
$env:AI_ASSISTANT_ENABLED = "1"
$env:AI_ASSISTANT_PROVIDER = "mock"
$env:USE_CLOUDINARY_MEDIA = "0"
$env:CELERY_TASK_ALWAYS_EAGER = "1"
$env:DEBUG = "True"
$env:ALLOWED_HOSTS = "127.0.0.1,localhost"

if ($FreshDb -and (Test-Path $SqlitePath)) {
    Remove-Item $SqlitePath -Force
    Write-Host "Removed $SqlitePath"
}

Write-Host "==> Isolation check"
python manage.py check_demo_isolation --require-demo

Write-Host "==> Migrate (SQLite config/db_demo.sqlite3)"
python manage.py migrate --noinput

if (-not $SkipSeed) {
    Write-Host "==> Seed showcase dataset"
    python manage.py seed_demo_showcase --reset
}

Write-Host ""
Write-Host "Zyntra DEMO ready."
Write-Host "  URL:      http://127.0.0.1:$Port/login/"
Write-Host "  Email:    demo@demo-system.com"
Write-Host "  Password: DemoShowcase2026!"
Write-Host "  DB file:  $SqlitePath"
Write-Host ""

python manage.py runserver "127.0.0.1:$Port"
