# Bootstrap a business project for orch multi-agent worktrees.
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_project.ps1 `
#     -ProjectName alpha -ProjectRoot D:\work\my-app [-SourceRepo D:\work\my-app-src]
#
# If -SourceRepo is omitted, ProjectRoot must already be a normal git repo with develop
# (or main will be used only to seed develop — prefer existing develop).

param(
    [Parameter(Mandatory = $true)][string]$ProjectName,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [string]$SourceRepo = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$RepoRoot;$env:PYTHONPATH" } else { $RepoRoot }

function Invoke-Orch {
    param([Parameter(ValueFromRemainingArguments = $true)]$Args)
    python -m orch @Args
    if ($LASTEXITCODE -ne 0) { throw "orch failed: $Args (exit $LASTEXITCODE)" }
}

$ProjectRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ProjectRoot)
New-Item -ItemType Directory -Force -Path $ProjectRoot | Out-Null
$Bare = Join-Path $ProjectRoot ".bare.git"

if (-not (Test-Path $Bare)) {
    if ($SourceRepo) {
        $SourceRepo = (Resolve-Path $SourceRepo).Path
        Write-Host "Cloning bare from $SourceRepo ..."
        git clone --bare $SourceRepo $Bare
    } else {
        # Use ProjectRoot if it already has .git
        $gitDir = Join-Path $ProjectRoot ".git"
        if (Test-Path $gitDir) {
            Write-Host "Creating bare from existing ProjectRoot git ..."
            $tmpBare = Join-Path $env:TEMP ("orch-bare-" + [guid]::NewGuid().ToString("n"))
            git clone --bare $ProjectRoot $tmpBare
            # Move into place carefully if ProjectRoot still has normal worktree
            if (Test-Path $Bare) { throw ".bare.git already exists" }
            Move-Item $tmpBare $Bare
            Write-Host "Note: keep/move your old working tree aside; orch uses main/ + worktrees/."
        } else {
            throw "No .bare.git and no -SourceRepo / existing .git. Provide a source repository."
        }
    }
    git --git-dir=$Bare config user.email "orch@local"
    git --git-dir=$Bare config user.name "orch"
}

# Ensure develop exists
$hasDevelop = $true
git --git-dir=$Bare show-ref --verify --quiet refs/heads/develop
if ($LASTEXITCODE -ne 0) {
    $hasDevelop = $false
    git --git-dir=$Bare show-ref --verify --quiet refs/heads/main
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Creating develop from main ..."
        git --git-dir=$Bare branch develop main
        $hasDevelop = $true
    }
}
if (-not $hasDevelop) {
    throw "Bare repo has neither develop nor main. Create develop first."
}

Write-Host "Registering project '$ProjectName' -> $ProjectRoot"
Invoke-Orch project add $ProjectName $ProjectRoot
Invoke-Orch $ProjectName init

Write-Host ""
Write-Host "Bootstrap OK."
Write-Host "  project: $ProjectName"
Write-Host "  root:    $ProjectRoot"
Write-Host "  main:    $(Join-Path $ProjectRoot 'main')"
Write-Host "  worktrees: $(Join-Path $ProjectRoot 'worktrees')"
Write-Host ""
Write-Host "Next:"
Write-Host "  orch $ProjectName worktree-add agentA feat/my-feature"
Write-Host "  opencode $(Join-Path $ProjectRoot 'worktrees\agentA-feat__my-feature')"
