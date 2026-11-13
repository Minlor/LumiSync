[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$RunTests,
    # Local development can skip the installer when Inno Setup is unavailable.
    [switch]$SkipInstaller,
    # Kept for workflow compatibility; the build itself is driven by uv.
    [string]$PythonCommand = "py",
    [string]$PythonVersion = "3.13",
    [string]$UvCommand = "uv"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $Root "build\windows"
$PyInstallerBuildRoot = Join-Path $Root "build\pyinstaller"
$VenvDir = Join-Path $BuildRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$DistDir = Join-Path $Root "dist"
$OnedirPath = Join-Path $DistDir "LumiSync"
$OnedirExe = Join-Path $OnedirPath "LumiSync.exe"
# Retained only to clean a stale portable build from an older release.
$OnefileExe = Join-Path $DistDir "LumiSync.exe"
$InnoScript = Join-Path $Root "packaging\windows\LumiSync.iss"

function Get-AppVersion {
    $init = Join-Path $Root "lumisync\__init__.py"
    $match = Select-String -Path $init -Pattern '__version__\s*=\s*"([^"]+)"'
    if (-not $match) {
        throw "Could not read __version__ from $init"
    }
    return $match.Matches[0].Groups[1].Value
}

function Find-Iscc {
    $command = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $bases = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $bases += (Join-Path $env:LOCALAPPDATA "Programs")
    }
    foreach ($base in $bases) {
        if ([string]::IsNullOrWhiteSpace($base)) {
            continue
        }
        $directories = Get-ChildItem -Path $base -Directory -Filter "Inno Setup *" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($directory in $directories) {
            $candidate = Join-Path $directory.FullName "ISCC.exe"
            if (Test-Path $candidate) {
                return $candidate
            }
        }
    }
    return $null
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Assert-UnderRoot {
    param([string]$Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    if (-not $full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside repository root: $full"
    }
}

function Remove-RepoPath {
    param([string]$Path)

    if (Test-Path $Path) {
        Assert-UnderRoot $Path
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Invoke-SmokeCheck {
    param([string]$ExePath)

    if (-not (Test-Path $ExePath)) {
        throw "Expected executable was not created: $ExePath"
    }

    foreach ($argument in @("--help", "--check-integrations", "--check-gui")) {
        $process = Start-Process `
            -FilePath $ExePath `
            -ArgumentList $argument `
            -PassThru `
            -WindowStyle Hidden

        if (-not $process.WaitForExit(30000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Smoke check $argument timed out for $ExePath"
        }

        $process.Refresh()

        if ($process.ExitCode -ne 0) {
            throw "Smoke check $argument failed for $ExePath with exit code $($process.ExitCode)"
        }
    }
}

if ([Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "Windows packaging must be run on Windows."
}

$OriginalLocation = Get-Location
Set-Location $Root

try {
if ($Clean) {
    Write-Step "Cleaning previous Windows packaging output"
    Remove-RepoPath $BuildRoot
    Remove-RepoPath $PyInstallerBuildRoot
    Remove-RepoPath $OnedirPath
    Remove-RepoPath $OnefileExe
    Remove-RepoPath (Join-Path $DistDir "SHA256SUMS.txt")
    Remove-RepoPath (Join-Path $DistDir "LumiSync-Windows-SHA256SUMS.txt")
    Remove-RepoPath (Join-Path $DistDir "LumiSync-Setup.exe")
    Get-ChildItem -Path $DistDir -Filter "LumiSync-*-Setup.exe" -File -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-RepoPath $_.FullName }
}

if (-not (Get-Command $UvCommand -ErrorAction SilentlyContinue)) {
    throw "uv is required for reproducible builds. Install it from https://docs.astral.sh/uv/ and rerun."
}

Write-Step "Installing the locked build environment"
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
$env:UV_PROJECT_ENVIRONMENT = $VenvDir
try {
    $syncArguments = @("sync", "--frozen", "--no-dev", "--project", $Root, "--group", "build")
    $python = $PythonVersion -replace '^-', ''
    if (-not [string]::IsNullOrWhiteSpace($python)) {
        $syncArguments += @("--python", $python)
    }
    Invoke-Checked $UvCommand $syncArguments
}
finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
}
if (-not (Test-Path $VenvPython)) {
    throw "uv did not create the expected build environment at $VenvDir"
}

if ($RunTests) {
    Write-Step "Running unit tests"
    # -t imports tests as a package, so tests/__init__.py installs the per-test
    # watchdog and keeps test logs out of the user's log folder.
    Invoke-Checked $VenvPython @("-m", "unittest", "discover", "-s", (Join-Path $Root "tests"), "-t", $Root)
}

Write-Step "Building onedir bundle"
Invoke-Checked $VenvPython @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $PyInstallerBuildRoot "onedir"),
    (Join-Path $Root "packaging\pyinstaller\lumisync_onedir.spec")
)

Write-Step "Running executable smoke checks"
Invoke-SmokeCheck $OnedirExe

if (-not $SkipInstaller) {
    Write-Step "Building Inno Setup installer"
    $Iscc = Find-Iscc
    if (-not $Iscc) {
        throw "Inno Setup 6 (ISCC.exe) is required for the installer. Install it from https://jrsoftware.org/isinfo.php or pass -SkipInstaller."
    }
    $Version = Get-AppVersion
    # Inno's VersionInfoVersion must be numeric; a pre-release suffix stays in
    # the display name but is stripped here.
    $NumericVersion = ($Version -replace '[^0-9.].*$', '')
    Invoke-Checked $Iscc @(
        "/Qp",
        "/DMyAppVersion=$Version",
        "/DMyAppVersionNumeric=$NumericVersion",
        $InnoScript
    )
    $Installer = Join-Path $DistDir "LumiSync-Setup.exe"
    if (-not (Test-Path $Installer)) {
        throw "Expected installer was not created: $Installer"
    }
    $installerSize = (Get-Item $Installer).Length
    if ($installerSize -lt 5MB) {
        throw "Installer looks incomplete (${installerSize} bytes): $Installer"
    }
}

if (-not $SkipInstaller) {
    Write-Step "Generating SHA-256 checksums"
    # Only the installer is published. The manifest has a per-platform name
    # because the Linux release attaches its own to the same GitHub release.
    $artifacts = Get-ChildItem -Path $DistDir -File -Filter "LumiSync-Setup.exe"
    $checksumLines = foreach ($artifact in $artifacts) {
        $hash = (Get-FileHash $artifact.FullName -Algorithm SHA256).Hash.ToLower()
        "$hash  $($artifact.Name)"
    }
    $checksumLines | Set-Content (Join-Path $DistDir "LumiSync-Windows-SHA256SUMS.txt") -Encoding ascii
}

Write-Step "Build complete"
Write-Host "Onedir bundle: $OnedirPath"
if (-not $SkipInstaller) {
    Write-Host "Installer: $Installer"
    Write-Host "Checksums: $(Join-Path $DistDir 'LumiSync-Windows-SHA256SUMS.txt')"
} else {
    Write-Host "Installer and checksums skipped (-SkipInstaller)"
}
}
finally {
    Set-Location $OriginalLocation
}
