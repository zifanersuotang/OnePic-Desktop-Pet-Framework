param(
    [switch]$IncludeUserAssets,
    [string]$PackageUserAssetsRoot = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\utf8.ps1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pyinstaller = Join-Path $projectRoot ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $pyinstaller)) {
    throw "尚未安装构建环境，请先运行 scripts\setup_environment.ps1。"
}
Set-Location $projectRoot

if ($IncludeUserAssets) {
    if (-not $PackageUserAssetsRoot) {
        $PackageUserAssetsRoot = Join-Path $projectRoot "user_assets"
    }
    $resolvedPackageRoot = (Resolve-Path -LiteralPath $PackageUserAssetsRoot).Path
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedPackageRoot "active-pet.json"))) {
        throw "打包素材缺少 active-pet.json。"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedPackageRoot "pet\manifest.json"))) {
        throw "打包素材缺少 pet\manifest.json。"
    }
    $env:ONEPIC_INCLUDE_USER_ASSETS = "1"
    $env:ONEPIC_PACKAGE_USER_ROOT = $resolvedPackageRoot
    if (Test-Path -LiteralPath (Join-Path $resolvedPackageRoot "selfie.png")) {
        $env:ONEPIC_INCLUDE_SELFIE_ASSET = "1"
    } else {
        $env:ONEPIC_INCLUDE_SELFIE_ASSET = "0"
    }
} else {
    $env:ONEPIC_INCLUDE_USER_ASSETS = "0"
    Remove-Item Env:ONEPIC_PACKAGE_USER_ROOT -ErrorAction SilentlyContinue
    $env:ONEPIC_INCLUDE_SELFIE_ASSET = "0"
}

& $pyinstaller --noconfirm --clean OnePicDesktopPet.spec
exit $LASTEXITCODE
