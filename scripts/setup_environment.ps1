$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

& "$PSScriptRoot\check_environment.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "[OnePic] 环境检查失败（退出码 $LASTEXITCODE）。"
}
Set-Location $projectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
    } else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw "[OnePic] 创建 .venv 失败（退出码 $LASTEXITCODE）。"
    }
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "[OnePic] pip 升级失败（退出码 $LASTEXITCODE）。"
}
& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "[OnePic] 依赖安装失败（退出码 $LASTEXITCODE）。"
}
Write-Host "[OnePic] 项目环境已经安装到 .venv。"




