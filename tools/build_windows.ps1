[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$RunTests,
    [string]$PythonCommand = "py",
    [string]$PythonVersion = "-3.13"
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
$PortableZip = Join-Path $DistDir "LumiSync-Windows-x64-portable.zip"
$OnefileExe = Join-Path $DistDir "LumiSync-Windows-x64-onefile.exe"

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

function Invoke-BasePython {
    param([string[]]$Arguments)

    if ([string]::IsNullOrWhiteSpace($PythonVersion)) {
        Invoke-Checked $PythonCommand $Arguments
    }
    else {
        $allArguments = @($PythonVersion) + $Arguments
        Invoke-Checked $PythonCommand $allArguments
    }
}

function Invoke-SmokeCheck {
    param([string]$ExePath)

    if (-not (Test-Path $ExePath)) {
        throw "Expected executable was not created: $ExePath"
    }

    $process = Start-Process `
        -FilePath $ExePath `
        -ArgumentList "--help" `
        -PassThru `
        -WindowStyle Hidden

    if (-not $process.WaitForExit(30000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "Smoke check timed out for $ExePath"
    }

    $process.Refresh()

    if ($process.ExitCode -ne 0) {
        throw "Smoke check failed for $ExePath with exit code $($process.ExitCode)"
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
    Remove-RepoPath $PortableZip
    Remove-RepoPath $OnefileExe
}

Write-Step "Creating isolated build environment"
if (-not (Test-Path $VenvPython)) {
    New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
    Invoke-BasePython @("-m", "venv", $VenvDir)
}

Write-Step "Installing build dependencies"
Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
Invoke-Checked $VenvPython @("-m", "pip", "install", "-e", $Root, "pyinstaller")

if ($RunTests) {
    Write-Step "Running unit tests"
    Invoke-Checked $VenvPython @("-m", "unittest", "discover", "-s", (Join-Path $Root "tests"))
}

Write-Step "Building portable app folder"
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

Write-Step "Creating portable zip"
Remove-RepoPath $PortableZip
Compress-Archive -Path $OnedirPath -DestinationPath $PortableZip -CompressionLevel Optimal

Write-Step "Running executable smoke checks"
Invoke-SmokeCheck $OnedirExe
Invoke-SmokeCheck $OnefileExe

Write-Step "Build complete"
Write-Host "Portable zip: $PortableZip"
Write-Host "Onefile exe:  $OnefileExe"
}
finally {
    Set-Location $OriginalLocation
}
