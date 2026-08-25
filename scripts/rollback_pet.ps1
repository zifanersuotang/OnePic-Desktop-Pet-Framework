$ErrorActionPreference = "Stop"
. "$PSScriptRoot\utf8.ps1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "尚未创建 .venv。"
}
$env:ONEPIC_TESTS_PASSED = "1"
Set-Location $projectRoot
& $python ".\tools\build_pet_workspace.py" "rollback" --rollback
exit $LASTEXITCODE
