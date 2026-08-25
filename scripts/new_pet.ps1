param(
    [Parameter(Mandatory = $true)]
    [string]$PetId,
    [string]$DisplayName = "",
    [string]$Source = "",
    [string]$SelfieFrame = "",
    [ValidateSet("full-body", "half-body", "animal", "vehicle-seated", "static")]
    [string]$Mode = "full-body",
    [ValidateSet("sprite", "hybrid-live2d")]
    [string]$RenderingMode = "sprite",
    [ValidateRange(120, 600)]
    [int]$RecommendedDisplayHeight = 220
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\utf8.ps1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Run scripts\setup_environment.ps1 first."
}

$arguments = @(
    (Join-Path $projectRoot "tools\pet_workspace.py"),
    "init",
    $PetId,
    "--display-name",
    $DisplayName,
    "--recommended-display-height",
    $RecommendedDisplayHeight,
    "--mode",
    $Mode,
    "--rendering-mode",
    $RenderingMode
)
if ($Source) {
    $arguments += @("--source", $Source)
}
if ($SelfieFrame) {
    $arguments += @("--selfie-frame", $SelfieFrame)
}

Set-Location $projectRoot
& $python @arguments
exit $LASTEXITCODE
