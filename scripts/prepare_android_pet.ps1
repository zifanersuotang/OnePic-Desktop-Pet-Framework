param(
    [Parameter(Mandatory = $false)]
    [string]$PetId = "gugugaga2"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "缺少项目虚拟环境，请先按项目规则运行 setup_environment.ps1。"
}

Push-Location $projectRoot
try {
    & $python ".\tools\prepare_android_pet.py" --pet-id $PetId
    if ($LASTEXITCODE -ne 0) {
        throw "Android 宠物素材装配失败，退出码：$LASTEXITCODE"
    }
} finally {
    Pop-Location
}
