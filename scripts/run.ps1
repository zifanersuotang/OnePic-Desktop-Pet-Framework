$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "尚未创建 .venv，请先运行 scripts\setup_environment.ps1。"
}
& $python (Join-Path $projectRoot "main.py") @args




