param(
    [switch]$UnitOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$mappedDrive = $null
$testRoot = $projectRoot
$savedJavaHome = [Environment]::GetEnvironmentVariable("JAVA_HOME", "Process")

$javaHomeExecutable = if ([string]::IsNullOrWhiteSpace($env:JAVA_HOME)) {
    $null
} else {
    Join-Path $env:JAVA_HOME "bin\java.exe"
}
if ($null -eq $javaHomeExecutable -or -not (Test-Path -LiteralPath $javaHomeExecutable)) {
    $studioJbr = "C:\Program Files\Android\Android Studio\jbr"
    if (Test-Path -LiteralPath (Join-Path $studioJbr "bin\java.exe")) {
        $env:JAVA_HOME = $studioJbr
    }
}

try {
    if ($projectRoot -match "[^\x00-\x7F]") {
        $usedDrives = @(
            Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name.ToUpperInvariant() }
        )
        $mappedDrive = @("Z", "Y", "X", "W", "V", "U", "T", "S", "R", "Q") |
            Where-Object { $_ -notin $usedDrives } |
            Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace($mappedDrive)) {
            throw "No free drive letter is available for the Gradle non-ASCII path workaround."
        }
        & subst.exe "${mappedDrive}:" $projectRoot
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath "${mappedDrive}:\android")) {
            throw "Could not create the temporary ASCII drive mapping for Android tests."
        }
        $testRoot = "${mappedDrive}:"
    }

    $mappedAndroidRoot = Join-Path $testRoot "android"
    $wrapper = Join-Path $mappedAndroidRoot "gradlew.bat"
    $tasks = @(":app:testDebugUnitTest")
    if (-not $UnitOnly) {
        $tasks += @(":app:lintDebug", ":app:assembleDebug")
    }

    Push-Location $mappedAndroidRoot
    try {
        & $wrapper @tasks
        if ($LASTEXITCODE -ne 0) {
            throw "Android verification failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
} finally {
    if ($null -ne $mappedDrive) {
        & subst.exe "${mappedDrive}:" /D | Out-Null
    }
    if ($null -eq $savedJavaHome) {
        Remove-Item -LiteralPath "Env:JAVA_HOME" -ErrorAction SilentlyContinue
    } else {
        $env:JAVA_HOME = $savedJavaHome
    }
}
