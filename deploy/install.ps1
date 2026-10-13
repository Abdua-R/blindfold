#Requires -RunAsAdministrator
# Installs blindfold for the current Windows user. Run from the repository root in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File deploy\install.ps1
param([string]$Source = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = "Stop"

$venv = Join-Path $env:LOCALAPPDATA "blindfold\venv"
python -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install "$Source[ner]"
# Fetch the name model once now, so the proxy never needs the network for it. Failure only warns:
# with use_ner on, the proxy then runs without the name model and tells the user so.
try {
    & "$venv\Scripts\python.exe" -c "from gliner2 import GLiNER2; GLiNER2.from_pretrained('fastino/gliner2-multi-v1')"
    if ($LASTEXITCODE -ne 0) { Write-Warning "blindfold: the name model could not be downloaded." }
} catch {
    Write-Warning "blindfold: the name model could not be downloaded: $_"
}
$scripts = "$venv\Scripts"
$exe = Join-Path $scripts "blindfold.exe"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$scripts*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$scripts", "User")
}

$cfgDir = Join-Path $env:USERPROFILE ".blindfold"
New-Item -ItemType Directory -Force $cfgDir | Out-Null
$cfgFile = Join-Path $cfgDir "config.json"
if (-not (Test-Path $cfgFile)) {
    [IO.File]::WriteAllText($cfgFile, '{"llm_base_url": "", "llm_model": "gpt-oss-120b", "llm_timeout_s": 15, "use_ner": false}',
        (New-Object Text.UTF8Encoding $false))
}

$skillDir = Join-Path $env:USERPROFILE ".claude\skills\blindfold"
New-Item -ItemType Directory -Force $skillDir | Out-Null
Copy-Item (Join-Path $Source "src\blindfold\skill\SKILL.md") $skillDir -Force

$ms = Get-Content (Join-Path $Source "deploy\managed-settings.json") -Raw | ConvertFrom-Json
$ms.hooks.PreToolUse[0].hooks[0].command = $exe
$target = "C:\Program Files\ClaudeCode"
New-Item -ItemType Directory -Force $target | Out-Null
[IO.File]::WriteAllText((Join-Path $target "managed-settings.json"), ($ms | ConvertTo-Json -Depth 10),
    (New-Object Text.UTF8Encoding $false))

$action = New-ScheduledTaskAction -Execute $exe -Argument "serve"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName "blindfold" -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "blindfold"

Write-Host "blindfold installed."
Write-Host "Set llm_base_url in $cfgFile to your private model (or a local Ollama, http://127.0.0.1:11434/v1)."
Write-Host "Each user: sign in with /login, turn off 'Help improve Claude' in claude.ai privacy settings,"
Write-Host "and keep raw data files out of git."
