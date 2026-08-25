param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$failures = @()

Write-Host "[OnePic Android] 检查 JDK、Android SDK 与构建工具..."

$java = Get-Command java -ErrorAction SilentlyContinue
$javac = Get-Command javac -ErrorAction SilentlyContinue
$studioJbr = "C:\Program Files\Android\Android Studio\jbr\bin"
if (($null -eq $java -or $null -eq $javac) -and (Test-Path -LiteralPath $studioJbr)) {
    $studioJava = Join-Path $studioJbr "java.exe"
    $studioJavac = Join-Path $studioJbr "javac.exe"
    if ((Test-Path -LiteralPath $studioJava) -and (Test-Path -LiteralPath $studioJavac)) {
        $java = Get-Item -LiteralPath $studioJava
        $javac = Get-Item -LiteralPath $studioJavac
    }
}
if ($null -eq $java -or $null -eq $javac) {
    $failures += "缺少 JDK 17（java/javac）"
} else {
    $javaPath = if ($null -ne $java.Source) { $java.Source } else { $java.FullName }
    Write-Host "[通过] Java：$javaPath"
}

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
if ([string]::IsNullOrWhiteSpace($sdkRoot) -or -not (Test-Path -LiteralPath $sdkRoot)) {
    $failures += "缺少 ANDROID_SDK_ROOT/ANDROID_HOME 指向的 Android SDK"
} else {
    Write-Host "[通过] Android SDK：$sdkRoot"
    $platform36 = Join-Path $sdkRoot "platforms\android-36"
    $platform361 = Join-Path $sdkRoot "platforms\android-36.1"
    $buildTools = Join-Path $sdkRoot "build-tools\36.0.0"
    if (
        -not (Test-Path -LiteralPath $platform36) -and
        -not (Test-Path -LiteralPath $platform361)
    ) {
        $failures += "缺少 Android 16 平台（platforms/android-36 或 android-36.1）"
    }
    if (-not (Test-Path -LiteralPath $buildTools)) {
        $failures += "缺少 Android Build Tools 36.0.0"
    }
}

$gradleWrapper = Join-Path $projectRoot "android\gradlew.bat"
$gradle = Get-Command gradle -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $gradleWrapper) -and $null -eq $gradle) {
    $failures += "缺少 Gradle 9.5 或完整 Gradle Wrapper"
} elseif (Test-Path -LiteralPath $gradleWrapper) {
    Write-Host "[通过] Gradle Wrapper：$gradleWrapper"
} else {
    Write-Host "[通过] Gradle：$($gradle.Source)"
}

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) {
        Write-Host "[缺少] $failure" -ForegroundColor Yellow
    }
    Write-Host "[停止] 未安装任何软件，也未修改环境变量。" -ForegroundColor Yellow
    exit 2
}

Write-Host "[通过] Android 构建环境完整。"
exit 0
