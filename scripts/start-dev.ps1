<#
.SYNOPSIS
  Start Fleet Studio for local development: the current version (main), the updated version (a feature
  branch checked out as a git worktree), or both side by side.

.DESCRIPTION
  Each version runs from its own checkout, with its own ports and its own dev database:

    current   branch main                      API http://127.0.0.1:8080   web http://localhost:5173
    updated   branch feature/mission-library   API http://127.0.0.1:8081   web http://localhost:5174

  The API and the web dev server of each version open in their own windows; close a window (or choose
  "stop") to stop that server. A port already in use is reported and left alone, so running the script
  twice never starts a second copy.

  Only the current version (8080) is reachable by the lab host's Fleet Studio Agent through the SSH
  reverse tunnel (ssh -N -R 127.0.0.1:18080:127.0.0.1:8080 hermes@<host>).

.PARAMETER Which
  current, updated, both or stop. Omit it to be asked.

.PARAMETER Branch
  The branch of the updated version (default feature/mission-library).

.EXAMPLE
  .\start-dev.cmd
  .\start-dev.cmd both
#>
param(
    [ValidateSet('', 'current', 'updated', 'both', 'stop')]
    [string]$Which = '',
    [string]$Branch = 'feature/mission-library'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

# ------------------------------------------------------------------ where each version lives

function Get-Worktrees {
    $list = @()
    $cur = $null
    foreach ($line in (git -C $repo worktree list --porcelain)) {
        if ($line -like 'worktree *') { $cur = @{ Path = ($line.Substring(9) -replace '/', '\'); Branch = '' }; $list += $cur }
        elseif ($line -like 'branch refs/heads/*' -and $cur) { $cur.Branch = $line.Substring(18) }
    }
    return $list
}

$worktrees = Get-Worktrees
$mainTree = $worktrees | Where-Object { $_.Branch -eq 'main' } | Select-Object -First 1
if (-not $mainTree) { $mainTree = $worktrees[0] }
$featureTree = $worktrees | Where-Object { $_.Branch -eq $Branch } | Select-Object -First 1

$versions = [ordered]@{
    current = @{ Name = 'current'; Label = 'Current'; Branch = $mainTree.Branch; Path = $mainTree.Path; Api = 8080; Web = 5173 }
}
if ($featureTree) {
    $versions.updated = @{ Name = 'updated'; Label = 'Updated'; Branch = $featureTree.Branch; Path = $featureTree.Path; Api = 8081; Web = 5174 }
}

function Find-Python($path) {
    foreach ($candidate in @((Join-Path $path '.venv\Scripts\python.exe'), (Join-Path $mainTree.Path '.venv\Scripts\python.exe'))) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "No Python virtual environment found (.venv). Create one in $($mainTree.Path) first."
}

function Test-Listening([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Listening([int]$port, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Listening $port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-Window([string]$title, [string]$workdir, [string]$command) {
    $script = "`$host.UI.RawUI.WindowTitle = '$title'; Set-Location -LiteralPath '$workdir'; $command"
    Start-Process powershell -WorkingDirectory $workdir -ArgumentList @('-NoExit', '-NoProfile', '-Command', $script) | Out-Null
}

# ------------------------------------------------------------------ first run of the updated version

function Initialize-Data($v) {
    if ($v.Name -eq 'current') { return }
    $db = Join-Path $v.Path '.fleetcontrol-dev.db'
    $srcDb = Join-Path $mainTree.Path '.fleetcontrol-dev.db'
    if ((Test-Path $db) -or -not (Test-Path $srcDb)) { return }
    Write-Host ""
    Write-Host "The $($v.Label.ToLower()) version has no dev database yet."
    $answer = Read-Host "Copy the current version's dev data (people, instances, blueprints) into it? [Y/n]"
    if ($answer -eq '' -or $answer -match '^[Yy]') {
        Copy-Item $srcDb $db
        $creds = Join-Path $mainTree.Path '.fleetcontrol-dev-credentials.json'
        if (Test-Path $creds) { Copy-Item $creds (Join-Path $v.Path '.fleetcontrol-dev-credentials.json') }
        Write-Host "  Copied. Sign in with the same accounts as the current version."
    } else {
        Write-Host "  Starting empty: the API creates an admin; its password lands in $($v.Path)\.fleetcontrol-dev-credentials.json"
    }
}

# ------------------------------------------------------------------ start and stop

function Start-Version($v) {
    Write-Host ""
    Write-Host "$($v.Label) version ($($v.Branch)) from $($v.Path)" -ForegroundColor Cyan
    Initialize-Data $v
    $python = Find-Python $v.Path

    if (Test-Listening $v.Api) {
        Write-Host "  API: port $($v.Api) is already in use; leaving it alone"
    } else {
        Start-Window "Fleet Studio API - $($v.Label) :$($v.Api)" $v.Path "`$env:PORT = '$($v.Api)'; & '$python' scripts\dev_api.py"
        Write-Host "  API: starting on http://127.0.0.1:$($v.Api)"
    }

    $web = Join-Path $v.Path 'apps\web'
    if (Test-Listening $v.Web) {
        Write-Host "  Web: port $($v.Web) is already in use; leaving it alone"
    } else {
        $install = "if (-not (Test-Path node_modules)) { Write-Host 'Installing web dependencies (first run)...'; npm install }; "
        $envs = "`$env:FLEETCONTROL_WEB_PORT = '$($v.Web)'; `$env:FLEETCONTROL_API_PORT = '$($v.Api)'; "
        Start-Window "Fleet Studio web - $($v.Label) :$($v.Web)" $web ($envs + $install + 'npm run dev')
        Write-Host "  Web: starting on http://localhost:$($v.Web)"
    }
}

function Stop-Version($v) {
    foreach ($port in @($v.Api, $v.Web)) {
        $pids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $pids) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
        if ($pids) { Write-Host "  stopped port $port" } else { Write-Host "  port $port was not running" }
    }
}

# ------------------------------------------------------------------ menu

Write-Host ""
Write-Host "Fleet Studio - local development" -ForegroundColor White
foreach ($v in $versions.Values) {
    $state = if (Test-Listening $v.Api) { 'running' } else { 'stopped' }
    '  {0,-8} {1,-28} API :{2}  web http://localhost:{3}  ({4})' -f $v.Label, $v.Branch, $v.Api, $v.Web, $state | Write-Host
}
if (-not $featureTree) {
    Write-Host "  (no worktree for branch ${Branch}: only the current version is available)" -ForegroundColor Yellow
}

if (-not $Which) {
    Write-Host ""
    Write-Host "  1  Start the current version"
    if ($featureTree) { Write-Host "  2  Start the updated version"; Write-Host "  3  Start both" }
    Write-Host "  s  Stop a version"
    Write-Host "  q  Quit"
    $choice = Read-Host "Which one"
    switch ($choice) {
        '1' { $Which = 'current' }
        '2' { $Which = 'updated' }
        '3' { $Which = 'both' }
        { $_ -in 's', 'S' } { $Which = 'stop' }
        default { return }
    }
}

if ($Which -in 'updated', 'both' -and -not $featureTree) { throw "No worktree for branch $Branch." }

if ($Which -eq 'stop') {
    $target = Read-Host "Stop which: 1 current, 2 updated, 3 both"
    $names = switch ($target) { '1' { @('current') } '2' { @('updated') } '3' { @('current', 'updated') } default { @() } }
    foreach ($n in $names) { if ($versions.Contains($n)) { Write-Host "$($versions[$n].Label):"; Stop-Version $versions[$n] } }
    return
}

$chosen = switch ($Which) { 'current' { @('current') } 'updated' { @('updated') } 'both' { @('current', 'updated') } }
foreach ($n in $chosen) { Start-Version $versions[$n] }

Write-Host ""
Write-Host "Waiting for the servers..."
foreach ($n in $chosen) {
    $v = $versions[$n]
    $apiUp = Wait-Listening $v.Api 60
    $webUp = Wait-Listening $v.Web 180
    if ($apiUp -and $webUp) {
        Write-Host "  $($v.Label): http://localhost:$($v.Web)" -ForegroundColor Green
        Start-Process "http://localhost:$($v.Web)"
    } else {
        Write-Host "  $($v.Label): not up yet (API $apiUp, web $webUp); check its windows" -ForegroundColor Yellow
    }
}
Write-Host ""
Write-Host "Sign-in passwords: .fleetcontrol-dev-credentials.json in each version's folder."
