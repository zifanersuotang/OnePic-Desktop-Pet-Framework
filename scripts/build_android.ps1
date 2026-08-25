param(
    [Parameter(Mandatory = $false)]
    [string]$PetId = "gugugaga2",

    [Parameter(Mandatory = $false)]
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug",

    [Parameter(Mandatory = $false)]
    [string]$DisplayName = "",

    [Parameter(Mandatory = $false)]
    [string]$ApplicationId = "",

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 2100000000)]
    [int]$VersionCode = 1,

    [Parameter(Mandatory = $false)]
    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]*$")]
    [string]$VersionName = "0.1.0"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($PetId -notmatch "^[a-z0-9][a-z0-9_-]*$") {
    throw "PetId 只能包含小写字母、数字、短横线和下划线，并且必须以字母或数字开头。"
}

$runtimeManifest = Join-Path $projectRoot "user_assets\pets\$PetId\runtime\manifest.json"
if ([string]::IsNullOrWhiteSpace($DisplayName)) {
    if (-not (Test-Path -LiteralPath $runtimeManifest)) {
        throw "缺少宠物运行清单，无法读取显示名称：$runtimeManifest"
    }
    $runtimeConfig = Get-Content -LiteralPath $runtimeManifest -Encoding UTF8 -Raw |
        ConvertFrom-Json
    $DisplayName = [string]$runtimeConfig.display_name
}
if ([string]::IsNullOrWhiteSpace($DisplayName)) {
    throw "DisplayName 不能为空。"
}
if ($DisplayName.Contains("`r") -or $DisplayName.Contains("`n")) {
    throw "DisplayName 不能包含换行符。"
}

if ([string]::IsNullOrWhiteSpace($ApplicationId)) {
    if ($PetId -eq "gugugaga2") {
        # 保留首个正式包的既有身份，确保后续升级不会变成另一个应用。
        $ApplicationId = "com.onepic.desktop.pet"
    } else {
        $applicationSuffix = $PetId.Replace("-", "_")
        if ($applicationSuffix -match "^[0-9]") {
            $applicationSuffix = "pet_$applicationSuffix"
        }
        $ApplicationId = "com.onepic.desktop.pet.$applicationSuffix"
    }
}
if ($ApplicationId -notmatch "^(?:[A-Za-z][A-Za-z0-9_]*\.)+[A-Za-z][A-Za-z0-9_]*$") {
    throw "ApplicationId 不是有效的 Android 应用包名：$ApplicationId"
}

$javaHomeExecutable = if ([string]::IsNullOrWhiteSpace($env:JAVA_HOME)) {
    $null
} else {
    Join-Path $env:JAVA_HOME "bin\java.exe"
}
if ($null -eq $javaHomeExecutable -or -not (Test-Path -LiteralPath $javaHomeExecutable)) {
    $studioJbr = "C:\Program Files\Android\Android Studio\jbr"
    if (Test-Path -LiteralPath (Join-Path $studioJbr "bin\java.exe")) {
        # 只修改当前脚本及其 Gradle 子进程，不写入系统环境变量。
        $env:JAVA_HOME = $studioJbr
    }
}

Push-Location $projectRoot
try {
    & ".\scripts\check_android_environment.ps1"
    if ($LASTEXITCODE -ne 0) {
        throw "Android 构建环境未就绪。"
    }
    & ".\scripts\prepare_android_pet.ps1" -PetId $PetId

    $task = if ($Configuration -eq "Release") { "assembleRelease" } else { "assembleDebug" }
    $wrapper = Join-Path $projectRoot "android\gradlew.bat"
    $gradleArguments = @(
        "-p",
        ".\android",
        $task,
        "-PonepicApplicationId=$ApplicationId",
        "-PonepicDisplayName=$DisplayName",
        "-PonepicVersionCode=$VersionCode",
        "-PonepicVersionName=$VersionName"
    )
    if (Test-Path -LiteralPath $wrapper) {
        & $wrapper @gradleArguments
    } else {
        & gradle @gradleArguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Android Gradle 构建失败，退出码：$LASTEXITCODE"
    }

    $variant = $Configuration.ToLowerInvariant()
    $apk = Join-Path $projectRoot "android\app\build\outputs\apk\$variant\app-$variant.apk"
    if (-not (Test-Path -LiteralPath $apk)) {
        throw "构建命令完成，但没有找到 APK：$apk"
    }

    $namedApk = Join-Path (
        Split-Path -Parent $apk
    ) "$PetId-$VersionName-$variant.apk"
    Copy-Item -LiteralPath $apk -Destination $namedApk -Force
    $apkHash = (Get-FileHash -LiteralPath $namedApk -Algorithm SHA256).Hash

    # 只做离线制品校验，不在构建阶段调用 adb 或等待设备。
    $sdkRoot = $env:ANDROID_SDK_ROOT
    if ([string]::IsNullOrWhiteSpace($sdkRoot)) {
        $sdkRoot = $env:ANDROID_HOME
    }
    if ([string]::IsNullOrWhiteSpace($sdkRoot)) {
        $defaultSdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
        if (Test-Path -LiteralPath $defaultSdk) {
            $sdkRoot = $defaultSdk
        }
    }
    if ([string]::IsNullOrWhiteSpace($sdkRoot)) {
        throw "无法定位 Android SDK，不能执行离线 APK 校验。"
    }
    $buildToolsRoot = Join-Path $sdkRoot "build-tools"
    $aapt = Get-ChildItem -LiteralPath $buildToolsRoot -Recurse -Filter "aapt.exe" |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    $apkSigner = Get-ChildItem -LiteralPath $buildToolsRoot -Recurse -Filter "apksigner.bat" |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    if ([string]::IsNullOrWhiteSpace($aapt) -or [string]::IsNullOrWhiteSpace($apkSigner)) {
        throw "Android build-tools 缺少 aapt.exe 或 apksigner.bat。"
    }

    # 旧版 aapt 在 Windows 上不能稳定接收含中文的绝对路径；切到 APK 目录后
    # 只传 ASCII 文件名，避免把合法制品误判为 Illegal byte sequence。
    $apkDirectory = Split-Path -Parent $namedApk
    $apkFileName = Split-Path -Leaf $namedApk
    Push-Location $apkDirectory
    try {
        $badging = & $aapt dump badging ".\$apkFileName" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "APK 身份校验失败：$($badging -join [Environment]::NewLine)"
        }
        $packageLine = [string]($badging | Select-Object -First 1)
        $expectedPackagedId = if ($Configuration -eq "Debug") {
            "$ApplicationId.debug"
        } else {
            $ApplicationId
        }
        if ($packageLine -notmatch "name='$([regex]::Escape($expectedPackagedId))'") {
            throw "APK 包名与预期不一致：$packageLine"
        }

        $signatureCheck = & $apkSigner verify --verbose ".\$apkFileName" 2>&1
        if ($LASTEXITCODE -ne 0 -or $signatureCheck -notcontains "Verifies") {
            throw "APK 签名校验失败：$($signatureCheck -join [Environment]::NewLine)"
        }
    } finally {
        Pop-Location
    }

    $report = [ordered]@{
        schema_version = 1
        generated_at_utc = [DateTime]::UtcNow.ToString("o")
        pet_id = $PetId
        display_name = $DisplayName
        application_id = $ApplicationId
        version_code = $VersionCode
        version_name = $VersionName
        configuration = $Configuration
        apk_file = Split-Path -Leaf $namedApk
        apk_size_bytes = (Get-Item -LiteralPath $namedApk).Length
        apk_sha256 = $apkHash
        packaged_application_id = $expectedPackagedId
        offline_manifest_verification = "passed"
        offline_signature_verification = "passed"
        device_probe = "not-run"
        privacy_mode = "share-no-selfie"
    }
    $reportPath = Join-Path (Split-Path -Parent $apk) "onepic-build-report.json"
    $report | ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host "[完成] 应用身份：$ApplicationId"
    Write-Host "[完成] APK：$namedApk"
    Write-Host "[完成] SHA-256：$apkHash"
    Write-Host "[完成] 离线清单与签名校验通过（未调用 adb）"
    Write-Host "[完成] 构建报告：$reportPath"
} finally {
    Pop-Location
}
