param(
    [switch]$UnitOnly,
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\utf8.ps1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "尚未创建 .venv，请先运行 scripts\setup_environment.ps1。"
}

$environmentNames = @(
    "ONEPIC_TEST_SANDBOX",
    "ONEPIC_USER_ROOT",
    "ONEPIC_SETTINGS_DIR",
    "LOCALAPPDATA",
    "QT_QPA_PLATFORM",
    "ONEPIC_USE_DEMO_ASSETS"
)
$savedEnvironment = @{}
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        "Process"
    )
}

$runId = "{0}-{1}" -f (Get-Date -Format "yyyyMMddTHHmmssfff"), $PID
$sandbox = Join-Path $projectRoot ".test-sandbox\runs\$runId"
$testExit = 1
$pytestExit = 1
$smokeExit = $null
try {
    $env:ONEPIC_TEST_SANDBOX = $sandbox
    $env:ONEPIC_USER_ROOT = Join-Path $sandbox "user_assets"
    $env:ONEPIC_SETTINGS_DIR = Join-Path $sandbox "settings"
    $env:LOCALAPPDATA = Join-Path $sandbox "localappdata"
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:ONEPIC_USE_DEMO_ASSETS = "1"
    New-Item -ItemType Directory -Path $sandbox -Force | Out-Null

    Set-Location $projectRoot
    & $python -m pytest -q -p no:cacheprovider
    $pytestExit = $LASTEXITCODE
    $testExit = $pytestExit
    if ($testExit -eq 0 -and -not $UnitOnly) {
        & $python main.py --smoke-test-ms 1500
        $smokeExit = $LASTEXITCODE
        $testExit = $smokeExit
    }
}
finally {
    foreach ($name in $environmentNames) {
        $saved = $savedEnvironment[$name]
        if ($null -eq $saved) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -LiteralPath "Env:$name" -Value $saved
        }
    }
}
if (-not [string]::IsNullOrWhiteSpace($ReportPath)) {
    if ([IO.Path]::IsPathRooted($ReportPath)) {
        $reportTarget = [IO.Path]::GetFullPath($ReportPath)
    }
    else {
        $reportTarget = [IO.Path]::GetFullPath((
            Join-Path $projectRoot $ReportPath
        ))
    }
    $reportParent = Split-Path -Parent $reportTarget
    New-Item -ItemType Directory -Path $reportParent -Force | Out-Null
    $report = [ordered]@{
        schema_version = 1
        status = if ($testExit -eq 0) { "passed" } else { "failed" }
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        unit_only = [bool]$UnitOnly
        pytest_exit_code = [int]$pytestExit
        smoke_exit_code = $smokeExit
    }
    $report | ConvertTo-Json -Depth 4 | Set-Content `
        -LiteralPath $reportTarget `
        -Encoding UTF8
}
exit $testExit
