param(
    [Parameter(Mandatory = $true)]
    [string]$PetId,
    [ValidateSet(
        "private-local",
        "share-with-approved-selfie",
        "share-no-selfie"
    )]
    [string]$Mode = "share-no-selfie"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\utf8.ps1"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "尚未创建 .venv，请先运行 scripts\setup_environment.ps1。"
}
Set-Location $projectRoot

$savedSettingsRoot = $env:ONEPIC_SETTINGS_DIR
$savedCompanionDataRoot = $env:ONEPIC_COMPANION_DATA_DIR
$savedDemoAssets = $env:ONEPIC_USE_DEMO_ASSETS
$savedQtPlatform = $env:QT_QPA_PLATFORM
$savedUserRoot = $env:ONEPIC_USER_ROOT
$savedPetManifest = $env:ONEPIC_PET_MANIFEST
$savedPetCatalog = $env:ONEPIC_PET_CATALOG
$savedBreakTestMode = $env:ONEPIC_BREAK_TEST_MODE
$savedTestsPassed = $env:ONEPIC_TESTS_PASSED
$savedIncludeUserAssets = $env:ONEPIC_INCLUDE_USER_ASSETS
$savedPackageUserRoot = $env:ONEPIC_PACKAGE_USER_ROOT
$savedIncludeSelfieAsset = $env:ONEPIC_INCLUDE_SELFIE_ASSET
$releaseExitCode = 0
$runId = "{0}-{1}" -f (Get-Date -Format "yyyyMMddTHHmmssfff"), $PID
$testReport = Join-Path `
    $projectRoot `
    ".test-sandbox\release-smoke\$runId\unit-tests\report.json"

try {
Remove-Item Env:ONEPIC_USER_ROOT -ErrorAction SilentlyContinue
Remove-Item Env:ONEPIC_PET_MANIFEST -ErrorAction SilentlyContinue
Remove-Item Env:ONEPIC_PET_CATALOG -ErrorAction SilentlyContinue
Remove-Item Env:ONEPIC_BREAK_TEST_MODE -ErrorAction SilentlyContinue
$defaultSettingsPath = Join-Path $projectRoot "config\settings.json"
$defaultSettingsData = Get-Content `
    -LiteralPath $defaultSettingsPath `
    -Raw `
    -Encoding UTF8 | ConvertFrom-Json
if ([bool]$defaultSettingsData.movement_region_enabled) {
    throw "分享包的活动区域默认开关必须关闭。"
}
if ($null -ne $defaultSettingsData.movement_region) {
    throw "分享包不得预置制作者机器的活动区域坐标。"
}

& $python ".\tools\pet_workspace.py" verify-runtime-review $PetId
if ($LASTEXITCODE -ne 0) {
    throw "当前 runtime 未绑定最终用户视觉验收，拒绝激活或发布。"
}
& "$PSScriptRoot\build_pet.ps1" `
    -PetId $PetId `
    -Activate `
    -Existing `
    -TestReportPath $testReport
if ($LASTEXITCODE -ne 0) { throw "桌宠构建与激活失败。" }

$smokeSettings = Join-Path $projectRoot ".test-sandbox\release-smoke\$runId\source-settings"
$smokeCompanionData = Join-Path $projectRoot ".test-sandbox\release-smoke\$runId\source-companion"
$env:ONEPIC_SETTINGS_DIR = $smokeSettings
$env:ONEPIC_COMPANION_DATA_DIR = $smokeCompanionData
Remove-Item Env:ONEPIC_USE_DEMO_ASSETS -ErrorAction SilentlyContinue
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue

$hybridPetWindowSmokePassed = $false
$hybridPetWindowSmokeReport = $null
$activeManifest = Join-Path $projectRoot "user_assets\pet\manifest.json"
$activeManifestData = Get-Content -LiteralPath $activeManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$actionMenuDisplayHeight = 360
if (
    $null -ne $activeManifestData.recommended_display_height -and
    [int]$activeManifestData.recommended_display_height -gt 0
) {
    $actionMenuDisplayHeight = [int]$activeManifestData.recommended_display_height
}
$actionMenuSmokeRoot = Join-Path $projectRoot ".test-sandbox\release-smoke\$runId\interaction-menu"
$actionMenuSmokeReport = Join-Path $actionMenuSmokeRoot "report.json"
& $python ".\tools\smoke_interaction_menu.py" `
    --manifest $activeManifest `
    --output $actionMenuSmokeReport `
    --capture (Join-Path $actionMenuSmokeRoot "interaction-menu.png") `
    --display-height $actionMenuDisplayHeight
if ($LASTEXITCODE -ne 0) {
    throw "互动动作菜单去重与真实点击烟测失败。"
}
if (
    $null -ne $activeManifestData.hybrid_runtime -and
    $activeManifestData.hybrid_runtime.enabled
) {
    $hybridDisplayHeight = 360
    if (
        $null -ne $activeManifestData.recommended_display_height -and
        [int]$activeManifestData.recommended_display_height -gt 0
    ) {
        $hybridDisplayHeight = [int]$activeManifestData.recommended_display_height
    }
    $hybridSmokeRoot = Join-Path $projectRoot ".test-sandbox\release-smoke\$runId\hybrid-window"
    $hybridPetWindowSmokeReport = Join-Path $hybridSmokeRoot "report.json"
    & $python ".\tools\smoke_hybrid_pet_window.py" `
        --manifest $activeManifest `
        --output $hybridPetWindowSmokeReport `
        --capture-dir (Join-Path $hybridSmokeRoot "captures") `
        --display-height $hybridDisplayHeight
    if ($LASTEXITCODE -ne 0) {
        throw "真实 PetWindow 混合待机烟测失败。"
    }
    & $python ".\tools\smoke_live2d_state_map.py" `
        --manifest $activeManifest `
        --output (Join-Path $hybridSmokeRoot "state-map-report.json") `
        --capture-dir (Join-Path $hybridSmokeRoot "state-map-captures") `
        --display-height $hybridDisplayHeight
    if ($LASTEXITCODE -ne 0) {
        throw "Live2D 全状态尺度与透明合成烟测失败。"
    }
    $activeRuntimeRoot = (Resolve-Path (
        Split-Path -Parent $activeManifest
    )).Path
    $accessoryManifestValue = [string](
        $activeManifestData.hybrid_runtime.accessory_manifest
    )
    if (-not [string]::IsNullOrWhiteSpace($accessoryManifestValue)) {
        $activeRuntimePrefix = $activeRuntimeRoot.TrimEnd('\') + '\'
        $accessoryRuntimePath = [IO.Path]::GetFullPath((
            Join-Path $activeRuntimeRoot $accessoryManifestValue
        ))
        if (
            -not $accessoryRuntimePath.StartsWith(
                $activeRuntimePrefix,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            -not (Test-Path -LiteralPath $accessoryRuntimePath -PathType Leaf)
        ) {
            throw "Live2D 常驻配件清单缺失或越界。"
        }
        $accessoryRuntimeData = Get-Content `
            -LiteralPath $accessoryRuntimePath `
            -Raw `
            -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $accessoryRuntimeData.binding) {
            $petWorkspace = (Resolve-Path (
                Join-Path $projectRoot "user_assets\pets\$PetId"
            )).Path
            $petConfigPath = Join-Path $petWorkspace "pet-config.json"
            $petConfigData = Get-Content `
                -LiteralPath $petConfigPath `
                -Raw `
                -Encoding UTF8 | ConvertFrom-Json
            $accessoryQaPresetValue = [string](
                $petConfigData.hybrid_runtime.accessory_qa_preset_file
            )
            if ([string]::IsNullOrWhiteSpace($accessoryQaPresetValue)) {
                throw "Cubism Part 绑定配件缺少发布期 accessory_qa_preset_file。"
            }
            $petWorkspacePrefix = $petWorkspace.TrimEnd('\') + '\'
            $accessoryQaPresetPath = [IO.Path]::GetFullPath((
                Join-Path $petWorkspace $accessoryQaPresetValue
            ))
            if (
                -not $accessoryQaPresetPath.StartsWith(
                    $petWorkspacePrefix,
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                -not (Test-Path -LiteralPath $accessoryQaPresetPath -PathType Leaf)
            ) {
                throw "Live2D 配件姿态门禁预设缺失或越界。"
            }
            $accessoryQaRoot = Join-Path `
                $hybridSmokeRoot `
                "accessory-binding-captures"
            & $python ".\tools\capture_live2d_presets.py" `
                --runtime-root $activeRuntimeRoot `
                --runtime-manifest $activeManifest `
                --model $activeManifestData.hybrid_runtime.model `
                --accessory-manifest $accessoryManifestValue `
                --preset-file $accessoryQaPresetPath `
                --output-dir $accessoryQaRoot `
                --view-width 360 `
                --view-height $hybridDisplayHeight
            if ($LASTEXITCODE -ne 0) {
                throw "Live2D 猪鼻 Part56 绑定 1px 姿态门禁失败。"
            }
            $accessoryQaReport = Get-Content `
                -LiteralPath (Join-Path $accessoryQaRoot "capture-presets-report.json") `
                -Raw `
                -Encoding UTF8 | ConvertFrom-Json
            if (
                $null -eq $accessoryQaReport.maximum_nose_alignment_error_css_px -or
                [double]$accessoryQaReport.maximum_nose_alignment_error_css_px -gt 1.0
            ) {
                throw "Live2D 猪鼻最终 DOM 中心偏离 Part56 超过 1 CSS px。"
            }
        }
    }
    if (
        $null -ne $activeManifestData.companion_behaviors -and
        $null -ne $activeManifestData.companion_behaviors.auto_expression_rotation -and
        $activeManifestData.companion_behaviors.auto_expression_rotation.enabled
    ) {
        & $python ".\tools\smoke_auto_expression_rotation.py" `
            --manifest $activeManifest `
            --output (Join-Path $hybridSmokeRoot "auto-expression-report.json") `
            --capture-dir (Join-Path $hybridSmokeRoot "auto-expression-captures") `
            --display-height $hybridDisplayHeight
        if ($LASTEXITCODE -ne 0) {
            throw "Live2D 六连表情自动轮动烟测失败。"
        }
    }
    $hybridPetWindowSmokePassed = $true
}

$companionSmokeRoot = Join-Path `
    $projectRoot `
    ".test-sandbox\release-smoke\$runId\double-click-chat"
$sourceDoubleClickSmokeReport = Join-Path $companionSmokeRoot "report.json"
& $python ".\tools\smoke_double_click_chat.py" `
    --manifest $activeManifest `
    --output $sourceDoubleClickSmokeReport `
    --capture (Join-Path $companionSmokeRoot "compact-chat.png") `
    --display-height $actionMenuDisplayHeight
if ($LASTEXITCODE -ne 0) {
    throw "窗口双击快捷聊天与零联网烟测失败。"
}

$sourceSmokeReport = Join-Path `
    $projectRoot `
    ".test-sandbox\release-smoke\$runId\source-startup\report.json"
& $python ".\main.py" `
    --smoke-test-ms 1800 `
    --smoke-report $sourceSmokeReport `
    --smoke-expected-pet-id $PetId
if ($LASTEXITCODE -ne 0) { throw "源码烟测失败。" }

$packageStage = Join-Path $projectRoot ".release-staging\$PetId-$runId"
& $python ".\tools\release_pet.py" prepare `
    --pet-id $PetId `
    --mode $Mode `
    --output $packageStage
if ($LASTEXITCODE -ne 0) { throw "发布暂存准备失败。" }

& "$PSScriptRoot\build.ps1" `
    -IncludeUserAssets `
    -PackageUserAssetsRoot (Join-Path $packageStage "user_assets")
if ($LASTEXITCODE -ne 0) { throw "发布程序构建失败。" }

$packagedSettings = Join-Path $projectRoot ".test-sandbox\release-smoke\$runId\packaged-settings"
$packagedCompanionData = Join-Path $projectRoot ".test-sandbox\release-smoke\$runId\packaged-companion"
$env:ONEPIC_SETTINGS_DIR = $packagedSettings
$env:ONEPIC_COMPANION_DATA_DIR = $packagedCompanionData
$packagedSmokeReport = Join-Path `
    $projectRoot `
    ".test-sandbox\release-smoke\$runId\packaged-startup\report.json"
& ".\dist\OnePicDesktopPet\OnePicDesktopPet.exe" `
    --smoke-test-ms 2200 `
    --smoke-report $packagedSmokeReport `
    --smoke-expected-pet-id $PetId
if ($LASTEXITCODE -ne 0) { throw "打包后 EXE 烟测失败。" }
$packagedCompanionSmokeRoot = Join-Path `
    $projectRoot `
    ".test-sandbox\release-smoke\$runId\packaged-double-click-chat"
$packagedDoubleClickSmokeReport = Join-Path `
    $packagedCompanionSmokeRoot `
    "report.json"
& ".\dist\OnePicDesktopPet\OnePicDesktopPet.exe" `
    --smoke-double-click-chat-output `
    $packagedDoubleClickSmokeReport `
    --smoke-double-click-chat-capture `
    (Join-Path $packagedCompanionSmokeRoot "compact-chat.png") `
    --smoke-expected-pet-id $PetId
if ($LASTEXITCODE -ne 0) {
    throw "打包后 EXE 双击快捷聊天与零自动请求烟测失败。"
}

$releaseRoot = Join-Path $projectRoot "dist\releases\$PetId-$runId"
$finalizeArguments = @(
    ".\tools\release_pet.py",
    "finalize",
    "--pet-id", $PetId,
    "--mode", $Mode,
    "--distribution", ".\dist\OnePicDesktopPet",
    "--output-directory", $releaseRoot,
    "--preparation-report", (Join-Path $packageStage "package-preparation.json"),
    "--tests-report", $testReport,
    "--source-smoke-report", $sourceSmokeReport,
    "--packaged-smoke-report", $packagedSmokeReport,
    "--interaction-menu-smoke-report", $actionMenuSmokeReport,
    "--double-click-chat-smoke-report", $sourceDoubleClickSmokeReport,
    "--packaged-double-click-chat-smoke-report", $packagedDoubleClickSmokeReport,
    "--tests-passed",
    "--source-smoke-passed",
    "--packaged-smoke-passed",
    "--double-click-chat-smoke-passed",
    "--packaged-double-click-chat-smoke-passed"
)
if ($hybridPetWindowSmokePassed) {
    $finalizeArguments += "--hybrid-pet-window-smoke-passed"
    $finalizeArguments += @(
        "--hybrid-pet-window-smoke-report",
        $hybridPetWindowSmokeReport
    )
}
& $python @finalizeArguments
$releaseExitCode = $LASTEXITCODE
}
finally {
    if ($null -eq $savedSettingsRoot) {
        Remove-Item Env:ONEPIC_SETTINGS_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_SETTINGS_DIR = $savedSettingsRoot
    }
    if ($null -eq $savedCompanionDataRoot) {
        Remove-Item Env:ONEPIC_COMPANION_DATA_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_COMPANION_DATA_DIR = $savedCompanionDataRoot
    }
    if ($null -eq $savedDemoAssets) {
        Remove-Item Env:ONEPIC_USE_DEMO_ASSETS -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_USE_DEMO_ASSETS = $savedDemoAssets
    }
    if ($null -eq $savedQtPlatform) {
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    }
    else {
        $env:QT_QPA_PLATFORM = $savedQtPlatform
    }
    if ($null -eq $savedUserRoot) {
        Remove-Item Env:ONEPIC_USER_ROOT -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_USER_ROOT = $savedUserRoot
    }
    if ($null -eq $savedPetManifest) {
        Remove-Item Env:ONEPIC_PET_MANIFEST -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_PET_MANIFEST = $savedPetManifest
    }
    if ($null -eq $savedPetCatalog) {
        Remove-Item Env:ONEPIC_PET_CATALOG -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_PET_CATALOG = $savedPetCatalog
    }
    if ($null -eq $savedBreakTestMode) {
        Remove-Item Env:ONEPIC_BREAK_TEST_MODE -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_BREAK_TEST_MODE = $savedBreakTestMode
    }
    if ($null -eq $savedTestsPassed) {
        Remove-Item Env:ONEPIC_TESTS_PASSED -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_TESTS_PASSED = $savedTestsPassed
    }
    if ($null -eq $savedIncludeUserAssets) {
        Remove-Item Env:ONEPIC_INCLUDE_USER_ASSETS -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_INCLUDE_USER_ASSETS = $savedIncludeUserAssets
    }
    if ($null -eq $savedPackageUserRoot) {
        Remove-Item Env:ONEPIC_PACKAGE_USER_ROOT -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_PACKAGE_USER_ROOT = $savedPackageUserRoot
    }
    if ($null -eq $savedIncludeSelfieAsset) {
        Remove-Item Env:ONEPIC_INCLUDE_SELFIE_ASSET -ErrorAction SilentlyContinue
    }
    else {
        $env:ONEPIC_INCLUDE_SELFIE_ASSET = $savedIncludeSelfieAsset
    }
}
exit $releaseExitCode
