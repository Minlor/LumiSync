[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$RunTests,
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
$OnefileExe = Join-Path $DistDir "LumiSync.exe"

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

Write-Step "Building onedir bundle (packaging smoke check)"
Invoke-Checked $VenvPython @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $PyInstallerBuildRoot "onedir"),
    (Join-Path $Root "packaging\pyinstaller\lumisync_onedir.spec")
)

Write-Step "Building single-file executable"
Invoke-Checked $VenvPython @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $PyInstallerBuildRoot "onefile"),
    (Join-Path $Root "packaging\pyinstaller\lumisync_onefile.spec")
)

Write-Step "Running executable smoke checks"
Invoke-SmokeCheck $OnedirExe
Invoke-SmokeCheck $OnefileExe

Write-Step "Build complete"
Write-Host "Single-file exe: $OnefileExe"
}
finally {
    Set-Location $OriginalLocation
}
