$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\utf8.ps1"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "尚未创建 .venv，请先运行 scripts\setup_environment.ps1。"
}
Set-Location $projectRoot
& $python ".\tools\onepic_workflow.py" status
exit $LASTEXITCODE



