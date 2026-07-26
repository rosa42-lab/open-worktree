# Install global `orch` launcher for Windows (any cwd).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install_orch.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BinDir = Join-Path $env:USERPROFILE ".local\bin"
$OrchCmd = Join-Path $BinDir "orch.cmd"
$OrchPs1 = Join-Path $BinDir "orch.ps1"

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# orch.cmd: works from cmd.exe and most tools that call "orch"
$cmdBody = @"
@echo off
set "ORCH_HOME=$RepoRoot"
set "PYTHONPATH=%ORCH_HOME%;%PYTHONPATH%"
python -m orch %*
"@
Set-Content -Path $OrchCmd -Value $cmdBody -Encoding ASCII

# orch.ps1: PowerShell-friendly wrapper
$ps1Body = @"
`$env:ORCH_HOME = '$RepoRoot'
`$env:PYTHONPATH = if (`$env:PYTHONPATH) { "`$env:ORCH_HOME;`$env:PYTHONPATH" } else { `$env:ORCH_HOME }
python -m orch @args
exit `$LASTEXITCODE
"@
Set-Content -Path $OrchPs1 -Value $ps1Body -Encoding UTF8

# Ensure ~/.local/bin on User PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
$pathParts = $userPath -split ";" | Where-Object { $_ -and $_.Trim() -ne "" }
if ($pathParts -notcontains $BinDir) {
    $newPath = if ($userPath.Trim()) { "$BinDir;$userPath" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Added to User PATH: $BinDir"
} else {
    Write-Host "User PATH already contains: $BinDir"
}

# Also set User PYTHONPATH to repo (backup if wrapper is bypassed)
$userPy = [Environment]::GetEnvironmentVariable("PYTHONPATH", "User")
if (-not $userPy) { $userPy = "" }
$pyParts = $userPy -split ";" | Where-Object { $_ -and $_.Trim() -ne "" }
if ($pyParts -notcontains $RepoRoot) {
    $newPy = if ($userPy.Trim()) { "$RepoRoot;$userPy" } else { $RepoRoot }
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $newPy, "User")
    Write-Host "Added to User PYTHONPATH: $RepoRoot"
} else {
    Write-Host "User PYTHONPATH already contains: $RepoRoot"
}

# Current session
$env:Path = "$BinDir;$env:Path"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$RepoRoot;$env:PYTHONPATH" } else { $RepoRoot }

Write-Host ""
Write-Host "Installed:"
Write-Host "  $OrchCmd"
Write-Host "  $OrchPs1"
Write-Host "  ORCH_HOME / PYTHONPATH -> $RepoRoot"
Write-Host ""
Write-Host "Verify (this shell):"
& $OrchCmd --version
Write-Host ""
Write-Host "Note: open a NEW terminal / OpenCode session so User PATH is visible."
