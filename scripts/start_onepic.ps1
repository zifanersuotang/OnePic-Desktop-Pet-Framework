param(
    [Parameter(Mandatory = $true)]
    [string]$SourceImage
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "尚未创建 .venv，请先运行 scripts\setup_environment.ps1。"
}
if (-not (Test-Path -LiteralPath $SourceImage -PathType Leaf)) {
    throw "找不到上传原图：$SourceImage"
}
Set-Location $projectRoot
& $python ".\tools\onepic_workflow.py" init --source $SourceImage
exit $LASTEXITCODE



