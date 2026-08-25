param(
    [Parameter(Mandatory = $true)]
    [string]$PetId,
    [switch]$Activate,
    [switch]$Existing,
    [string]$TestReportPath = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\utf8.ps1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Run scripts\setup_environment.ps1 first."
}

$arguments = @(
    (Join-Path $projectRoot "tools\build_pet_workspace.py"),
    $PetId
)

Set-Location $projectRoot
if ($Existing -and -not $Activate) {
    throw "-Existing 只能与 -Activate 一起使用。"
}
if (-not $Existing) {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (-not $Activate) { exit 0 }
}

$savedUserRoot = $env:ONEPIC_USER_ROOT
$savedSettingsRoot = $env:ONEPIC_SETTINGS_DIR
$savedLocalAppData = $env:LOCALAPPDATA
$savedTestsPassed = $env:ONEPIC_TESTS_PASSED
$testArguments = @("-UnitOnly")
if (-not [string]::IsNullOrWhiteSpace($TestReportPath)) {
    $testArguments += @("-ReportPath", $TestReportPath)
}
& "$PSScriptRoot\test.ps1" @testArguments
$testExit = $LASTEXITCODE
if ($null -eq $savedUserRoot) { Remove-Item Env:ONEPIC_USER_ROOT -ErrorAction SilentlyContinue } else { $env:ONEPIC_USER_ROOT = $savedUserRoot }
if ($null -eq $savedSettingsRoot) { Remove-Item Env:ONEPIC_SETTINGS_DIR -ErrorAction SilentlyContinue } else { $env:ONEPIC_SETTINGS_DIR = $savedSettingsRoot }
if ($null -eq $savedLocalAppData) { Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue } else { $env:LOCALAPPDATA = $savedLocalAppData }
if ($testExit -ne 0) { exit $testExit }
$env:ONEPIC_TESTS_PASSED = "1"
& $python (Join-Path $projectRoot "tools\build_pet_workspace.py") `
    $PetId --activate-existing
$activationExit = $LASTEXITCODE
if ($null -eq $savedTestsPassed) {
    Remove-Item Env:ONEPIC_TESTS_PASSED -ErrorAction SilentlyContinue
}
else {
    $env:ONEPIC_TESTS_PASSED = $savedTestsPassed
}
exit $activationExit
