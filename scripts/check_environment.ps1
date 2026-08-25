$ErrorActionPreference = "Stop"

Write-Host "[OnePic] 检查 Windows 与开发工具..."
if ($env:OS -ne "Windows_NT") {
    throw "当前版本只支持 Windows 10/11。"
}

$pythonCommand = $null
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $version = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($version -eq "3.12") {
        $pythonCommand = $venvPython
    }
}
if (-not $pythonCommand -and (Get-Command py -ErrorAction SilentlyContinue)) {
    try {
        & py -3.12 --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = "py -3.12"
        }
    } catch {
        $pythonCommand = $null
    }
}
if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($version -eq "3.12") {
        $pythonCommand = "python"
    }
}
if (-not $pythonCommand) {
    throw "未找到 Python 3.12。请先从 python.org 安装，安装完成后重新运行本脚本。"
}

Write-Host "[通过] Python：$pythonCommand"
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host "[通过] Git：$(& git --version)"
} else {
    Write-Warning "未安装 Git；本地运行不受影响，发布 GitHub 前需要安装。"
}
Write-Host "[通过] 环境检查完成。脚本未修改系统配置。"



