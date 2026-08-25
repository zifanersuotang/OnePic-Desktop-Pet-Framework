param(
    [Parameter(Mandatory = $true)]
    [string]$PetId,

    [Parameter(Mandatory = $false)]
    [string]$DisplayName = "",

    [Parameter(Mandatory = $false)]
    [string]$AndroidApplicationId = "",

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 2100000000)]
    [int]$VersionCode = 1,

    [Parameter(Mandatory = $false)]
    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]*$")]
    [string]$VersionName = "0.1.0",

    [Parameter(Mandatory = $false)]
    [ValidateSet("Debug", "Release")]
    [string]$AndroidConfiguration = "Debug",

    [Parameter(Mandatory = $false)]
    [switch]$ReleaseWindows
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Run scripts\setup_environment.ps1 first."
}

Push-Location $projectRoot
try {
    & $python ".\tools\pet_workspace.py" validate $PetId
    if ($LASTEXITCODE -ne 0) {
        throw "Pet workspace validation failed."
    }

    if ($ReleaseWindows) {
        & ".\scripts\release_pet.ps1" -PetId $PetId -Mode "share-no-selfie"
    } else {
        & ".\scripts\build_pet.ps1" -PetId $PetId -Activate
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Windows pet build failed."
    }

    $androidArguments = @{
        PetId = $PetId
        Configuration = $AndroidConfiguration
        VersionCode = $VersionCode
        VersionName = $VersionName
    }
    if (-not [string]::IsNullOrWhiteSpace($DisplayName)) {
        $androidArguments["DisplayName"] = $DisplayName
    }
    if (-not [string]::IsNullOrWhiteSpace($AndroidApplicationId)) {
        $androidArguments["ApplicationId"] = $AndroidApplicationId
    }
    & ".\scripts\build_android.ps1" @androidArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Android pet build failed."
    }
} finally {
    Pop-Location
}
