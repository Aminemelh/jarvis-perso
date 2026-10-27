<#
.SYNOPSIS
  Lance le cerveau de Jarvis (s'il ne tourne pas déjà) et ouvre le client web.

.PARAMETER Background
  Démarrage automatique de Windows : lance le serveur sans ouvrir le navigateur.
.PARAMETER Token
  Token à transmettre au navigateur pour l'appairer (utilisé par install.ps1).
#>
param(
    [switch]$Background,
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Brain = Join-Path $Root "brain"
$Py = Join-Path $Brain ".venv\Scripts\python.exe"
$Logs = Join-Path $Brain "data\logs"

if (-not (Test-Path $Py)) {
    Write-Host "Jarvis n'est pas installé. Lance d'abord : powershell -ExecutionPolicy Bypass -File install.ps1" -ForegroundColor Red
    exit 1
}

# Port lu dans brain\.env (8765 par défaut)
$Port = 8765
$envFile = Join-Path $Brain ".env"
if (Test-Path $envFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
        if ($line -match "^\s*JARVIS_PORT=(\d+)") { $Port = [int]$Matches[1] }
    }
}
$Url = "http://localhost:$Port"

function Test-Jarvis {
    try {
        $r = Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch { return $false }
}

if (-not (Test-Jarvis)) {
    New-Item -ItemType Directory -Force -Path $Logs | Out-Null
    $stamp = Get-Date -Format "yyyy-MM-dd"
    $env:PYTHONIOENCODING = "utf-8"
    if ($Background) {
        # Démarrage avec Windows : aucune fenêtre, journal dans brain\data\logs
        Start-Process -FilePath $Py -ArgumentList "-m", "jarvis_brain", "serve" -WorkingDirectory $Brain `
            -WindowStyle Hidden -RedirectStandardError (Join-Path $Logs "jarvis-$stamp.log") `
            -RedirectStandardOutput (Join-Path $Logs "jarvis-$stamp.out.log")
        exit 0
    }
    Write-Host "Démarrage du cerveau de Jarvis (fenêtre réduite « Jarvis »)…" -ForegroundColor Cyan
    Start-Process -FilePath $Py -ArgumentList "-m", "jarvis_brain", "serve" -WorkingDirectory $Brain -WindowStyle Minimized
    Write-Host "Premier lancement : Whisper télécharge son modèle (~500 Mo), ça peut prendre quelques minutes."
    $deadline = (Get-Date).AddMinutes(20)
    while (-not (Test-Jarvis)) {
        if ((Get-Date) -gt $deadline) {
            Write-Host "Jarvis ne répond pas. Regarde la fenêtre réduite « python » pour l'erreur." -ForegroundColor Red
            exit 1
        }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 3
    }
    Write-Host ""
}

if ($Background) { exit 0 }

$target = $Url + "/"
if ($Token) { $target += "#token=" + $Token }  # le fragment n'est jamais envoyé au serveur
Write-Host "Jarvis est prêt : $Url" -ForegroundColor Green
Start-Process $target
