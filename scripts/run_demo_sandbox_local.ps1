# Zyntra DEMO + real QuickBooks SANDBOX (Intuit network).
# Requires .env.demo.sandbox.local (gitignored) with Client ID/Secret.
# Intuit redirect URI must be: http://localhost:8000/quickbooks/callback/
param(
    [switch]$SkipSeed,
    [switch]$FreshDb,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$AppRoot = Split-Path -Parent $PSScriptRoot
$SqlitePath = Join-Path $AppRoot "config\db_demo.sqlite3"
$ManagePy = Join-Path $AppRoot "manage.py"
$EnvFile = Join-Path $AppRoot ".env.demo.sandbox.local"

if (-not (Test-Path $ManagePy)) {
    Write-Error "manage.py not found under $AppRoot"
}
if (-not (Test-Path $EnvFile)) {
    Write-Error @"
Missing $EnvFile

Create it with QUICKBOOKS_CLIENT_ID / QUICKBOOKS_CLIENT_SECRET and:
  QUICKBOOKS_PROVIDER=sandbox
  QUICKBOOKS_ENVIRONMENT=sandbox
  QUICKBOOKS_REDIRECT_URI=http://localhost:$Port/quickbooks/callback/
"@
}

Set-Location $AppRoot

function Import-DotEnvFile {
    param([string]$Path)
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        Set-Item -Path "Env:$name" -Value $value
    }
}

Import-DotEnvFile -Path $EnvFile

# Force sandbox isolation for this session (never production / never mock).
$env:DEMO_MODE = "1"
$env:SHOWCASE_MODE = "1"
$env:DEMO_USE_SQLITE = "1"
$env:QUICKBOOKS_PROVIDER = "sandbox"
$env:QUICKBOOKS_ENVIRONMENT = "sandbox"
$env:QUICKBOOKS_REDIRECT_URI = "http://localhost:$Port/quickbooks/callback/"
$env:DEMO_DISABLE_OUTBOUND_EMAIL = "1"
$env:AI_ASSISTANT_PROVIDER = "mock"
$env:USE_CLOUDINARY_MEDIA = "0"
$env:CELERY_TASK_ALWAYS_EAGER = "1"
$env:DEBUG = "True"
$env:ALLOWED_HOSTS = "127.0.0.1,localhost"

if (-not $env:QUICKBOOKS_CLIENT_ID -or -not $env:QUICKBOOKS_CLIENT_SECRET) {
    Write-Error "QUICKBOOKS_CLIENT_ID / QUICKBOOKS_CLIENT_SECRET missing in .env.demo.sandbox.local"
}

if ($FreshDb -and (Test-Path $SqlitePath)) {
    Remove-Item $SqlitePath -Force
    Write-Host "Removed $SqlitePath"
}

Write-Host "==> Isolation check (sandbox provider)"
python manage.py check_demo_isolation --require-demo

Write-Host "==> Migrate (SQLite)"
python manage.py migrate --noinput

if (-not $SkipSeed) {
    Write-Host "==> Seed showcase dataset"
    python manage.py seed_demo_showcase --reset
}

Write-Host ""
Write-Host "Zyntra DEMO + QuickBooks SANDBOX ready."
Write-Host "  Open ONLY:  http://localhost:$Port/login/"
Write-Host "  (Do not use 127.0.0.1 - Intuit redirect is localhost)"
Write-Host "  Email:      demo@demo-system.com"
Write-Host "  Password:   DemoShowcase2026!"
Write-Host "  Next:       QuickBooks Center -> Connect"
Write-Host "  Intuit URI: http://localhost:$Port/quickbooks/callback/"
Write-Host ""

# Bind all interfaces but tell user to browse via localhost hostname.
python manage.py runserver "0.0.0.0:$Port"
