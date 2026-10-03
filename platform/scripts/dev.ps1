# Lance l'environnement de test local de Digital360 :
#   - PostgreSQL portable (port 5433), démarré s'il est arrêté
#   - migrations et configuration métier à jour
#   - API sur http://localhost:8000 (fenêtre dédiée)
#   - pages du frontend (digital360/) sur http://localhost:5500 (fenêtre dédiée)
#   - ouverture du navigateur
#
# Usage, depuis n'importe quel dossier :
#   powershell -ExecutionPolicy Bypass -File C:\Users\DELL\Documents\be-media-ai\platform\scripts\dev.ps1
# Pour tout arrêter : fermer les deux fenêtres « Digital360 ».

$ErrorActionPreference = "Stop"

$Platform = Split-Path -Parent $PSScriptRoot
$Repo = Split-Path -Parent $Platform
$Frontend = Join-Path $Repo "digital360"
$Python = Join-Path $Platform ".venv\Scripts\python.exe"
$PgBin = Join-Path $HOME ".local\pgsql16\pgsql\bin"
$PgData = Join-Path $HOME ".local\pgsql16\data"

$env:DATABASE_URL = "postgresql+asyncpg://digital360:digital360@127.0.0.1:5433/digital360_test"
# Le frontend est servi sur localhost:5500 : c'est la seule origine autorisée à appeler l'API
$env:CORS_ALLOWED_ORIGINS = "http://localhost:5500"

if (-not (Test-Path $Python)) {
    throw "Environnement Python absent ($Python). Voir platform/README.md."
}

Write-Host "1/4 PostgreSQL..." -ForegroundColor Cyan
& "$PgBin\pg_isready.exe" -h 127.0.0.1 -p 5433 | Out-Null
if ($LASTEXITCODE -ne 0) {
    & "$PgBin\pg_ctl.exe" -D $PgData -o "-p 5433 -c listen_addresses=127.0.0.1" `
        -l (Join-Path $HOME ".local\pgsql16\server.log") -w start | Out-Null
}
& "$PgBin\pg_isready.exe" -h 127.0.0.1 -p 5433

Write-Host "2/4 Migrations et configuration metier..." -ForegroundColor Cyan
Push-Location $Platform
try {
    & $Python -m alembic upgrade head
    & $Python -m digital360.cli seed-config
} finally {
    Pop-Location
}

Write-Host "3/4 API sur http://localhost:8000 (nouvelle fenetre)..." -ForegroundColor Cyan
$apiCommand = @"
`$host.UI.RawUI.WindowTitle = 'Digital360 - API (port 8000)'
`$env:DATABASE_URL = '$env:DATABASE_URL'
`$env:CORS_ALLOWED_ORIGINS = '$env:CORS_ALLOWED_ORIGINS'
Set-Location '$Platform'
& '$Python' -m uvicorn digital360.main:create_app --factory --host 127.0.0.1 --port 8000
"@
Start-Process powershell -ArgumentList "-NoExit", "-Command", $apiCommand

Write-Host "4/4 Frontend sur http://localhost:5500 (nouvelle fenetre)..." -ForegroundColor Cyan
$webCommand = @"
`$host.UI.RawUI.WindowTitle = 'Digital360 - Frontend (port 5500)'
Set-Location '$Frontend'
& '$Python' -m http.server 5500 --bind 127.0.0.1
"@
Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCommand

# Laisse le temps à l'API de démarrer avant d'ouvrir le navigateur
$ready = $false
for ($i = 0; $i -lt 30 -and -not $ready; $i++) {
    Start-Sleep -Seconds 1
    try {
        $status = (Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready -TimeoutSec 2).status
        $ready = $status -eq "ok"
    } catch { }
}
if ($ready) {
    Write-Host "API prete." -ForegroundColor Green
} else {
    Write-Host "L'API ne repond pas encore : regardez la fenetre 'Digital360 - API'." -ForegroundColor Yellow
}

Start-Process "http://localhost:5500/index.html"
Write-Host "Site : http://localhost:5500/index.html   |   API : http://localhost:8000/api/v1/docs" -ForegroundColor Green
