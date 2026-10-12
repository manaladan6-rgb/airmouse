# AirMouse Windows Bootstrap — v19 "Instant Deployment" (Phase 3, Option C)
#
# WHAT THIS IS
#   A transparent, readable install script for Windows 10/11 x64.
#   It installs Python (only if missing, via winget — you see and approve
#   every step), creates a dedicated virtual environment, installs the
#   `airmouse` package from PyPI over HTTPS, and starts `airmouse setup`.
#
# TRUST MODEL (read this — AirMouse never asks you to trust blindly)
#   * You can read this entire script before running it. There is no
#     obfuscation and no remote second stage.
#   * Package source: PyPI (pypi.org) over HTTPS via `pip`, resolving
#     wheels only (`--only-binary :all:`) — no compilers, no build tools.
#   * The ONE optional model download (Vosk en-US, ~41.2 MB) happens
#     LATER, inside `airmouse voice-setup`, only with your explicit
#     consent, SHA256+SHA512-verified before use.
#   * Nothing is downloaded from any other host by this script.
#   * You can pin/inspect the exact version with  .\airmouse-bootstrap.ps1 -Version 19.0.0
#
# USAGE (PowerShell)
#   Set-ExecutionPolicy -Scope Process Bypass   # for this session only
#   .\airmouse-bootstrap.ps1
#   .\airmouse-bootstrap.ps1 -Version 19.0.0    # pin a release
#   .\airmouse-bootstrap.ps1 -SkipPython        # Python already installed
#
# STATUS: shipped in v19; every behavior above is script-readable.
#   The end-to-end run on REAL Windows hardware is PHYSICAL_TEST_REQUIRED
#   (docs/V19_RELEASE_REPORT.md records what was and was not executed).

param(
    [string]$Version = "",
    [switch]$SkipPython,
    [switch]$NoSetup
)

$ErrorActionPreference = "Stop"

function Say($msg)  { Write-Host "  $msg" }
function Head($msg) { Write-Host "`n== $msg ==" -ForegroundColor Cyan }

Head "AirMouse bootstrap (Windows)"

# --- 1. Python ------------------------------------------------------------
$py = $null
foreach ($candidate in @("py", "python")) {
    try {
        $v = & $candidate -3 --version 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
    } catch { }
}

if ($py) {
    Say "Python found: $(& $py -3 --version)"
} elseif ($SkipPython) {
    Write-Host "  ! -SkipPython given but no usable Python 3 was found." -ForegroundColor Yellow
    Write-Host "  ! Install Python 3.9-3.13 from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  ! (check 'Add python.exe to PATH' in the installer)" -ForegroundColor Yellow
    exit 1
} else {
    Head "Python not found — installing via winget (you can abort now)"
    Say "command: winget install --id Python.Python.3.12 -e --source winget"
    Say "This installs the official CPython 3.12 from python.org."
    $answer = Read-Host "  Proceed? [y/N]"
    if ($answer -notmatch "^[Yy]") {
        Say "declined — install Python manually, then re-run this script"
        exit 1
    }
    & winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ! winget install failed — install Python 3.12 manually:" -ForegroundColor Yellow
        Write-Host "  ! https://www.python.org/downloads/  (check 'Add to PATH')" -ForegroundColor Yellow
        exit 1
    }
    # refresh PATH for this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $py = "py"
    Say "Python installed: $(& $py -3 --version)"
}

# --- 2. Dedicated virtual environment --------------------------------------
Head "Creating the AirMouse environment"
$airDir = Join-Path $env:LOCALAPPDATA "AirMouse"
New-Item -ItemType Directory -Force -Path $airDir | Out-Null
$venvDir = Join-Path $airDir "runtime"
& $py -3 -m venv $venvDir
if ($LASTEXITCODE -ne 0) { Write-Host "  ! venv creation failed" -ForegroundColor Yellow; exit 1 }
$pip = Join-Path $venvDir "Scripts\pip.exe"
$air = Join-Path $venvDir "Scripts\airmouse.exe"
Say "environment: $venvDir"

# --- 3. Install AirMouse from PyPI (wheels only — never compiles) ---------
Head "Installing AirMouse" 
$pipArgs = @("install", "--only-binary", ":all:", "--disable-pip-version-check", "--upgrade")
if ($Version) { $pipArgs += "airmouse==$Version" } else { $pipArgs += "airmouse" }
Say "command: pip $($pipArgs -join ' ')"
& $pip @pipArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ! installation failed — common fixes:" -ForegroundColor Yellow
    Write-Host "  !   * check your internet connection (pypi.org must be reachable)" -ForegroundColor Yellow
    Write-Host "  !   * retry after: python -m pip install --upgrade pip" -ForegroundColor Yellow
    exit 1
}
$installed = & $air --version
Say "installed: $installed"

# --- 4. First-run setup -----------------------------------------------------
if (-not $NoSetup) {
    Head "Running airmouse setup"
    Say "(nothing is installed or downloaded without your consent)"
    & $air setup
}

# --- 5. Done ----------------------------------------------------------------
Head "Ready"
Say "start AirMouse:        $air"
Say "test your hardware:    $air doctor"
Say "learn the basics:      $air wizard"
Say "add voice (optional):  $air voice-setup"
Say "if anything misbehaves: $air repair   |   $air doctor --advanced"
Write-Host ""
