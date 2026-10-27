<#
.SYNOPSIS
  Installe et configure Jarvis sur Windows, en une commande.

.DESCRIPTION
  1. Vérifie Python 3.12 (l'installe avec winget s'il manque)
  2. Crée l'environnement Python et installe les dépendances
  3. Crée brain\.env et te pose les questions utiles (clé API, Gmail, agenda)
  4. Télécharge la voix française
  5. Crée le token de ce PC et appaire le navigateur automatiquement
  6. Propose le démarrage automatique de Jarvis à l'ouverture de session
  7. Lance Jarvis

  Relancer le script ne casse rien : il saute ce qui est déjà fait.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
#>
param(
    [switch]$NoStart,   # ne pas lancer Jarvis à la fin
    [switch]$Repair     # recrée le token du PC (si le navigateur l'a perdu)
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Brain = Join-Path $Root "brain"
$Py = Join-Path $Brain ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Brain ".env"
$Voice = "fr_FR-tom-medium"
$Utf8 = New-Object System.Text.UTF8Encoding $false
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Info($text) { Write-Host "    $text" }
function Fail($text) { Write-Host "`n$text" -ForegroundColor Red; exit 1 }

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "La commande a échoué : $Exe $($Arguments -join ' ')" }
}

# ---------------------------------------------------------------- .env

function Read-EnvLines { [System.IO.File]::ReadAllLines($EnvFile, [System.Text.Encoding]::UTF8) }

function Get-EnvValue([string]$Key) {
    foreach ($line in Read-EnvLines) {
        if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") { return $Matches[1].Trim() }
    }
    return ""
}

function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = [System.Collections.Generic.List[string]](Read-EnvLines)
    $pattern = "^\s*#?\s*$([regex]::Escape($Key))="
    $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            if (-not $done) { $lines[$i] = "$Key=$Value"; $done = $true }
        }
    }
    if (-not $done) { $lines.Add("$Key=$Value") }
    # UTF-8 sans BOM : python-dotenv lit le fichier tel quel.
    [System.IO.File]::WriteAllLines($EnvFile, $lines, $Utf8)
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    return (New-Object System.Net.NetworkCredential("", $secure)).Password.Trim()
}

function Ask-YesNo([string]$Prompt) {
    $answer = Read-Host -Prompt "$Prompt (o/N)"
    return $answer -match "^(o|oui|y|yes)$"
}

# ---------------------------------------------------------------- 0. mise à jour du code

if (Test-Path (Join-Path $Root ".git")) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Step "Mise à jour du code"
        & git -C $Root pull --ff-only
        if ($LASTEXITCODE -ne 0) { Info "Mise à jour impossible (modifications locales ?). On continue avec la version actuelle." }
    }
}

# ---------------------------------------------------------------- 1. Python

Step "Python 3.12"
function Test-Python {
    try { & py -3.12 -c "import sys" 2>$null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}
if (-not (Test-Path $Py)) {
    if (-not (Test-Python)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail "Python 3.12 est introuvable. Installe-le depuis https://www.python.org/downloads/ (coche « Add python.exe to PATH »), puis relance ce script."
        }
        Info "Installation de Python 3.12 avec winget…"
        & winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        if (-not (Test-Python)) {
            Fail "Python vient d'être installé. Ferme PowerShell, rouvre-le, puis relance ce script."
        }
    }
    Ok "Python trouvé"

    Step "Environnement Python (brain\.venv)"
    Invoke-Checked "py" @("-3.12", "-m", "venv", (Join-Path $Brain ".venv"))
}
Ok "Environnement prêt"

# ---------------------------------------------------------------- 2. dépendances

Step "Installation des dépendances (quelques minutes la première fois)"
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", "pip")
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", (Join-Path $Brain "requirements.txt"))
Ok "Dépendances installées"

# ---------------------------------------------------------------- 3. configuration

Step "Configuration (brain\.env)"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Brain ".env.example") $EnvFile
    Ok "Fichier .env créé"
}

if (-not (Get-EnvValue "ANTHROPIC_API_KEY")) {
    Info "Clé API Claude : https://platform.claude.com -> API Keys (ajoute un peu de crédit et une limite de dépense)."
    Info "Laisse vide pour démarrer en mode test gratuit (Jarvis répète ce que tu dis)."
    $key = Read-Secret "Clé API Anthropic"
    if ($key) {
        if ($key -notmatch "^sk-ant-") { Info "Attention : une clé Anthropic commence normalement par sk-ant-." }
        Set-EnvValue "ANTHROPIC_API_KEY" $key
        Set-EnvValue "JARVIS_LLM" "claude"
        Ok "Clé enregistrée, cerveau : Claude"
    } else {
        Set-EnvValue "JARVIS_LLM" "echo"
        Ok "Mode test (echo). Relance ce script plus tard pour ajouter la clé."
    }
} else {
    Ok "Clé API déjà configurée"
}

if (-not (Get-EnvValue "JARVIS_MAIL_GMAIL_USER")) {
    if (Ask-YesNo "Brancher Gmail maintenant ? (il faut un mot de passe d'application, voir docs\CONNECTIONS.md)") {
        $user = Read-Host "Adresse Gmail"
        Info "Mot de passe d'application : https://myaccount.google.com/apppasswords (16 lettres)."
        $pass = (Read-Secret "Mot de passe d'application") -replace "\s", ""
        if ($user -and $pass) {
            Set-EnvValue "JARVIS_MAIL_GMAIL_HOST" "imap.gmail.com"
            Set-EnvValue "JARVIS_MAIL_GMAIL_USER" $user.Trim()
            Set-EnvValue "JARVIS_MAIL_GMAIL_PASSWORD" $pass
            Ok "Gmail configuré"
        }
    }
}

if (-not (Get-EnvValue "JARVIS_CALENDAR_PERSO_URL")) {
    if (Ask-YesNo "Brancher Google Agenda maintenant ? (adresse secrète au format iCal, voir docs\CONNECTIONS.md)") {
        $url = (Read-Secret "Adresse secrète iCal (se termine par .ics)").Trim()
        if ($url -match "^(https|webcal)://") {
            Set-EnvValue "JARVIS_CALENDAR_PERSO_URL" $url
            Ok "Agenda configuré"
        } elseif ($url) {
            Info "Adresse ignorée : elle doit commencer par https://"
        }
    }
}

# ---------------------------------------------------------------- 4. voix

Step "Voix française ($Voice)"
$voiceFile = Join-Path $Brain "models\piper\$Voice.onnx"
if (-not (Test-Path $voiceFile)) {
    Push-Location $Brain
    try { Invoke-Checked $Py @("-m", "piper.download_voices", $Voice, "--download-dir", "models/piper") }
    finally { Pop-Location }
}
Ok "Voix installée"

# ---------------------------------------------------------------- 5. token du PC

Step "Token de ce PC"
Push-Location $Brain
try {
    $devices = & $Py -m jarvis_brain token list
    $hasPc = [bool]($devices | Select-String -Pattern "^pc\s" -Quiet)
    if ($hasPc -and $Repair) {
        & $Py -m jarvis_brain token revoke pc | Out-Null
        $hasPc = $false
    }
    $token = ""
    if (-not $hasPc) {
        $out = & $Py -m jarvis_brain token add pc
        if ($LASTEXITCODE -ne 0) { Fail "Création du token impossible : $out" }
        $match = [regex]::Match(($out -join "`n"), "jv_[A-Za-z0-9_-]+")
        $token = $match.Value
        Ok "Token créé pour « pc » (le navigateur sera appairé automatiquement)"
    } else {
        Ok "Token déjà créé (si le navigateur demande un token : .\install.ps1 -Repair)"
    }
} finally { Pop-Location }

# ---------------------------------------------------------------- 6. démarrage automatique

$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Jarvis.lnk"
if (-not (Test-Path $startup)) {
    if (Ask-YesNo "Démarrer Jarvis automatiquement à l'ouverture de session Windows ?") {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($startup)
        $lnk.TargetPath = "powershell.exe"
        $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\scripts\start.ps1`" -Background"
        $lnk.WorkingDirectory = $Root
        $lnk.WindowStyle = 7  # réduit
        $lnk.Save()
        Ok "Jarvis démarrera tout seul à la prochaine ouverture de session"
    }
}

# ---------------------------------------------------------------- 7. lancement

Write-Host "`nInstallation terminée." -ForegroundColor Green
Info "Pour lancer Jarvis plus tard : double-clique sur Jarvis.cmd (dossier $Root)."
if (-not $NoStart) {
    & (Join-Path $Root "scripts\start.ps1") -Token $token
}
