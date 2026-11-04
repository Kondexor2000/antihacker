$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $repoRoot ".env.compose"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing .env.compose. Copy .env.compose.example and set its secret values first."
}

foreach ($line in Get-Content -LiteralPath $envFile) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
        continue
    }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
        Set-Item -Path "Env:$($parts[0].Trim())" -Value $parts[1]
    }
}

$env:DJANGO_DEBUG = "True"
$env:DJANGO_SECURE_SSL_REDIRECT = "False"
$env:DJANGO_DATABASE = "postgres"
$env:POSTGRES_HOST = "127.0.0.1"
$env:POSTGRES_PORT = "5433"
$env:SECURE_BACKUP_DIR = Join-Path $repoRoot "secure_backups"
$port = if ($env:WEB_PORT) { [int]$env:WEB_PORT } else { 8001 }
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $repoRoot
$worker = $null
try {
    docker compose --env-file $envFile stop web backup-worker
    if ($LASTEXITCODE -ne 0) { throw "Could not stop the containerized web and backup worker." }
    docker compose --env-file $envFile up -d db
    if ($LASTEXITCODE -ne 0) { throw "Could not start the PostgreSQL container." }

    & $python manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw "Database migrations failed." }

    $logDir = Join-Path $repoRoot "runtime-logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $worker = Start-Process -FilePath $python `
        -ArgumentList @("manage.py", "process_backups") `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput (Join-Path $logDir "backup-worker.out.log") `
        -RedirectStandardError (Join-Path $logDir "backup-worker.err.log")
    Start-Sleep -Seconds 1
    if ($worker.HasExited) {
        Get-Content (Join-Path $logDir "backup-worker.err.log") -ErrorAction SilentlyContinue
        throw "The host backup worker exited during startup."
    }

    Write-Host "Serving on http://0.0.0.0:$port/ (LAN clients will be logged by their source IP)."
    Write-Host "Backup worker PID: $($worker.Id). Logs: $logDir"
    Write-Host "Press Ctrl+C to stop the web server and its worker."
    & $python manage.py runserver "0.0.0.0:$port"
}
finally {
    if ($worker -and -not $worker.HasExited) {
        Stop-Process -Id $worker.Id -Force
    }
    Pop-Location
}
