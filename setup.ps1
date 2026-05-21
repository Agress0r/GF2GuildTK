$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$LogFile = Join-Path $ProjectRoot "setup.log"
$VenvDir = Join-Path $ProjectRoot "GF2TTK"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$MinFreeMb = 1500
$PythonInstallerVersion = "3.12.10"
$PythonInstallerFile = "python-$PythonInstallerVersion-amd64.exe"
$PythonInstallerUrl = "https://www.python.org/ftp/python/$PythonInstallerVersion/$PythonInstallerFile"

function Write-Log {
    param([string]$Message = "")
    Write-Host $Message
    Add-Content -LiteralPath $LogFile -Value $Message -Encoding UTF8
}

function Write-Warn {
    param([string]$Message)
    Write-Log "WARNING: $Message"
}

function Fail {
    param([int]$Code, [string]$Message)
    Write-Log ""
    Write-Log "ERROR: $Message"
    Write-Log "See details in: $LogFile"
    exit $Code
}

function Invoke-Logged {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$AllowFailure
    )

    Add-Content -LiteralPath $LogFile -Value ("> $FilePath $($Arguments -join ' ')") -Encoding UTF8
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments 2>&1 | ForEach-Object {
            Add-Content -LiteralPath $LogFile -Value $_.ToString() -Encoding UTF8
        }
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    $code = $LASTEXITCODE
    if (-not $AllowFailure -and $code -ne 0) {
        throw "Command failed with exit code ${code}: $FilePath $($Arguments -join ' ')"
    }
    return $code
}

function Get-BackupPath {
    param([Parameter(Mandatory=$true)][string]$Path)
    $candidate = "$Path.bak"
    $i = 0
    while (Test-Path -LiteralPath $candidate) {
        $i++
        $candidate = "$Path.bak.$i"
    }
    return $candidate
}

function Test-Python312 {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    try {
        $code = "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 42)"
        $output = & $FilePath @Arguments -c $code 2>> $LogFile
        $exitCode = $LASTEXITCODE
        $version = ($output | Select-Object -First 1)
        if ($exitCode -eq 0) {
            return [pscustomobject]@{
                Ok = $true
                Version = $version
                FilePath = $FilePath
                Arguments = $Arguments
                Display = ((@($FilePath) + $Arguments) -join " ").Trim()
            }
        }
        if ($exitCode -eq 42 -and $version) {
            Write-Log "  Ignoring $((@($FilePath) + $Arguments) -join ' '): Python $version is not 3.12.x"
            $script:BadPythonVersion = $version
        }
    } catch {
        Add-Content -LiteralPath $LogFile -Value "Python probe failed for ${FilePath}: $($_.Exception.Message)" -Encoding UTF8
    }

    return [pscustomobject]@{ Ok = $false }
}

function Find-Python312Candidate {
    $candidates = @(
        @{ FilePath = "py"; Args = @("-3.12") },
        @{ FilePath = "python"; Args = @() },
        @{ FilePath = "python3"; Args = @() },
        @{ FilePath = Join-Path $env:LocalAppData "Programs\Python\Python312\python.exe"; Args = @() },
        @{ FilePath = Join-Path $env:ProgramFiles "Python312\python.exe"; Args = @() },
        @{ FilePath = Join-Path ${env:ProgramFiles(x86)} "Python312\python.exe"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        if ($candidate.FilePath -like "*\*" -and -not (Test-Path -LiteralPath $candidate.FilePath)) {
            continue
        }
        $result = Test-Python312 -FilePath $candidate.FilePath -Arguments $candidate.Args
        if ($result.Ok) {
            Write-Log "  Found Python $($result.Version) using: $($result.Display)"
            return $result
        }
    }

    return [pscustomobject]@{ Ok = $false }
}

function Request-Python312Installer {
    Write-Log ""
    Write-Log "Python 3.12.x was not found."
    Write-Log "This project can download the official Python $PythonInstallerVersion Windows installer from python.org."
    Write-Log "URL: $PythonInstallerUrl"
    Write-Log ""
    Write-Log "The installer will open normally. This setup will not install Python silently."
    Write-Log "In the Python installer, enable: Add python.exe to PATH"
    Write-Log ""

    $answer = Read-Host "Download and open Python $PythonInstallerVersion installer now? [Y/N]"
    if ($answer -notmatch "^(y|yes|д|да)$") {
        return $false
    }

    $downloadDir = Join-Path $ProjectRoot ".downloads"
    New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
    $installerPath = Join-Path $downloadDir $PythonInstallerFile

    Write-Log "Downloading Python installer..."
    Write-Log "  $PythonInstallerUrl"
    Write-Log "  -> $installerPath"

    try {
        $oldProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $PythonInstallerUrl -OutFile $installerPath -UseBasicParsing
        $ProgressPreference = $oldProgress
    } catch {
        $ProgressPreference = $oldProgress
        Write-Log "Download failed: $($_.Exception.Message)"
        Write-Log "Manual download link: $PythonInstallerUrl"
        return $false
    }

    if (-not (Test-Path -LiteralPath $installerPath)) {
        Write-Log "Download failed: installer file was not created."
        return $false
    }

    $sizeMb = [math]::Round((Get-Item -LiteralPath $installerPath).Length / 1MB, 1)
    if ($sizeMb -lt 10) {
        Write-Log "Downloaded file is unexpectedly small ($sizeMb MB). Refusing to run it."
        return $false
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        Write-Log "Installer signature is not valid or not signed by Python Software Foundation."
        Write-Log "Signature status: $($signature.Status)"
        Write-Log "Signer: $($signature.SignerCertificate.Subject)"
        Write-Log "Refusing to run downloaded installer."
        return $false
    }
    Write-Log "  Authenticode signature OK: Python Software Foundation"

    Write-Log ""
    Write-Log "Opening Python installer. Finish installation, then return here."
    Write-Log "Reminder: enable Add python.exe to PATH."
    $process = Start-Process -FilePath $installerPath -Wait -PassThru
    Write-Log "Python installer exited with code $($process.ExitCode). Re-checking Python 3.12.x..."

    return $true
}

function Find-Python312 {
    Write-Log ""
    Write-Log "[2/8] Looking for Python 3.12.x..."

    $result = Find-Python312Candidate
    if ($result.Ok) { return $result }

    foreach ($name in @("python", "python3")) {
        try {
            $v = & $name -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) { $script:BadPythonVersion = ($v | Select-Object -First 1) }
        } catch {}
    }

    if (Request-Python312Installer) {
        $result = Find-Python312Candidate
        if ($result.Ok) { return $result }
        Write-Log "Python 3.12.x is still not visible to this setup after installer exit."
        Write-Log "Close this window, open a new terminal, and run setup.bat again."
    }

    if ($script:BadPythonVersion) {
        Fail 20 "Python $script:BadPythonVersion was found, but this project requires Python 3.12.x only. Install Python $PythonInstallerVersion from $PythonInstallerUrl and enable Add python.exe to PATH."
    }
    Fail 21 "Python 3.12.x was not found. Install Python $PythonInstallerVersion from $PythonInstallerUrl and enable Add python.exe to PATH."
}

function Invoke-Python {
    param(
        [Parameter(Mandatory=$true)]$Python,
        [string[]]$Arguments
    )
    Invoke-Logged -FilePath $Python.FilePath -Arguments ($Python.Arguments + $Arguments)
}

function Invoke-PipRetry {
    param([string[]]$Arguments)
    for ($i = 1; $i -le 3; $i++) {
        Write-Log "  pip attempt ${i}/3: $($Arguments -join ' ')"
        $code = Invoke-Logged -FilePath $VenvPython -Arguments (@("-m", "pip") + $Arguments + @("--disable-pip-version-check")) -AllowFailure
        if ($code -eq 0) { return $true }
        if ($i -lt 3) {
            Write-Log "  pip failed; retrying in a few seconds..."
            Start-Sleep -Seconds 5
        }
    }
    return $false
}

function Test-Venv312 {
    if (-not (Test-Path -LiteralPath $VenvPython)) { return $false }
    try {
        & $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 42)" *>> $LogFile
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

Set-Content -LiteralPath $LogFile -Encoding UTF8 -Value @(
    "============================================================",
    "Guild Tracker setup log",
    "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "Project: $ProjectRoot",
    "Required Python: 3.12.x only",
    "============================================================"
)

Write-Log "============================================================"
Write-Log "  Guild Tracker - Environment Setup"
Write-Log "  Required Python: 3.12.x only"
Write-Log "============================================================"

Write-Log ""
Write-Log "[1/8] Preflight checks..."
if (-not (Test-Path -LiteralPath "requirements.txt")) { Fail 10 "requirements.txt not found. Run setup.bat from the project root." }
if (-not (Test-Path -LiteralPath "main.py")) { Fail 11 "main.py not found. Run setup.bat from the project root." }
if (-not (Test-Path -LiteralPath "assets\badges\B1Gold.png")) { Write-Warn "Badge templates look incomplete: assets\badges\B1Gold.png is missing." }

try {
    $testFile = Join-Path $ProjectRoot ".setup_write_test.tmp"
    Set-Content -LiteralPath $testFile -Value "write-test" -Encoding ASCII
    Remove-Item -LiteralPath $testFile -Force
} catch {
    Fail 12 "Cannot write to the project folder. Move the project to a writable folder outside Program Files or protected sync folders."
}

$drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($ProjectRoot).Substring(0,1))
$freeMb = [math]::Floor($drive.Free / 1MB)
Write-Log "  Free space: $freeMb MB"
if ($freeMb -lt $MinFreeMb) { Fail 13 "Not enough free disk space. Need at least $MinFreeMb MB." }

if ($ProjectRoot -match "OneDrive|Dropbox|Google Drive") {
    Write-Warn "Project is inside a cloud-synced folder. If venv/model files are locked, pause sync or move the project."
}
Write-Log "  Preflight OK."

$Python = Find-Python312

Write-Log ""
Write-Log "[3/8] Preparing virtual environment: GF2TTK\"
if (Test-Path -LiteralPath $VenvPython) {
    if (Test-Venv312) {
        Write-Log "  Existing venv is Python 3.12.x - reusing it."
    } else {
        Write-Warn "Existing GF2TTK venv is broken or not Python 3.12.x."
        $backup = Get-BackupPath -Path $VenvDir
        Write-Log "  Moving old venv to: $backup"
        Move-Item -LiteralPath $VenvDir -Destination $backup
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Invoke-Python -Python $Python -Arguments @("-c", "import venv") | Out-Null
    Write-Log "  Creating venv..."
    Invoke-Python -Python $Python -Arguments @("-m", "venv", $VenvDir) | Out-Null
    if (-not (Test-Venv312)) { Fail 33 "venv was created, but it is not a working Python 3.12.x environment." }
}

Write-Log ""
Write-Log "[4/8] Checking pip..."
$pipCode = Invoke-Logged -FilePath $VenvPython -Arguments @("-m", "pip", "--version") -AllowFailure
if ($pipCode -ne 0) {
    Write-Log "  pip is missing; trying ensurepip..."
    Invoke-Logged -FilePath $VenvPython -Arguments @("-m", "ensurepip", "--upgrade") | Out-Null
}
Write-Log "  Upgrading pip/setuptools/wheel (non-fatal if blocked)..."
if (-not (Invoke-PipRetry -Arguments @("install", "--upgrade", "pip", "setuptools", "wheel"))) {
    Write-Warn "pip upgrade failed. Continuing with the existing pip."
}
$pipCode = Invoke-Logged -FilePath $VenvPython -Arguments @("-m", "pip", "--version") -AllowFailure
if ($pipCode -ne 0) { Fail 41 "pip does not work inside the venv." }

Write-Log ""
Write-Log "[5/8] Installing dependencies from requirements.txt..."
if (-not (Invoke-PipRetry -Arguments @("install", "-r", "requirements.txt"))) {
    Write-Log "Common causes:"
    Write-Log "  - no internet connection"
    Write-Log "  - corporate proxy/firewall blocks pip"
    Write-Log "  - antivirus blocks venv or ONNX/DLL files"
    Write-Log "  - package wheel is unavailable and native build tools are missing"
    Write-Log "For proxy networks, set HTTPS_PROXY and HTTP_PROXY, then rerun setup.bat."
    Fail 50 "Failed to install dependencies."
}
Write-Log "  Dependencies installed."

Write-Log ""
Write-Log "[6/8] Pre-loading OCR models..."
$warmCode = Invoke-Logged -FilePath $VenvPython -Arguments @("-c", "import os; os.environ['ORT_LOGGING_LEVEL']='3'; import ddddocr; ddddocr.DdddOcr(show_ad=False); from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('OCR warmup OK')") -AllowFailure
if ($warmCode -ne 0) {
    Write-Warn "OCR pre-load failed. Models may load on first app launch. If onnxruntime DLL loading failed, install Microsoft Visual C++ Redistributable 2015-2022 x64."
} else {
    Write-Log "  OCR models ready."
}

Write-Log ""
Write-Log "[7/8] Creating runtime folders and launchers..."
New-Item -ItemType Directory -Force -Path "debug_crops" | Out-Null
New-Item -ItemType Directory -Force -Path "config" | Out-Null

$runContent = @'
@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "GF2TTK\Scripts\python.exe" (
    echo.
    echo ERROR: Virtual environment not found. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

"GF2TTK\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 42)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: This venv is not Python 3.12.x. Run setup.bat again.
    echo.
    pause
    exit /b 2
)

echo Starting Guild Tracker...
"GF2TTK\Scripts\python.exe" main.py
set "APP_RC=%ERRORLEVEL%"
if not "%APP_RC%"=="0" (
    echo.
    echo ERROR: Application exited with code %APP_RC%.
    echo Check setup.log or run from cmd to see Python errors.
    echo.
    pause
)
exit /b %APP_RC%
'@

$runPath = Join-Path $ProjectRoot "run.bat"
$tmpRun = Join-Path $ProjectRoot ".setup_run.tmp"
Set-Content -LiteralPath $tmpRun -Value $runContent -Encoding ASCII
if (Test-Path -LiteralPath $runPath) {
    $old = Get-Content -LiteralPath $runPath -Raw -ErrorAction SilentlyContinue
    $new = Get-Content -LiteralPath $tmpRun -Raw
    if ($old -ne $new) {
        $backup = Get-BackupPath -Path $runPath
        Copy-Item -LiteralPath $runPath -Destination $backup
        Move-Item -LiteralPath $tmpRun -Destination $runPath -Force
        Write-Warn "Existing run.bat was backed up as $(Split-Path -Leaf $backup) and replaced."
    } else {
        Remove-Item -LiteralPath $tmpRun -Force
        Write-Log "  run.bat already up to date."
    }
} else {
    Move-Item -LiteralPath $tmpRun -Destination $runPath
    Write-Log "  Created run.bat."
}

if (Test-Path -LiteralPath "credentials.json") {
    Write-Log "  credentials.json found for Google Sheets integration."
} else {
    Write-Warn "credentials.json not found. Google Sheets sync will be unavailable until configured."
}

Write-Log ""
Write-Log "[8/8] Running smoke-test..."
$smoke = "import sys, os; assert sys.version_info[:2] == (3, 12); os.environ.setdefault('ORT_LOGGING_LEVEL','3'); import PIL, numpy, cv2, onnxruntime, ddddocr, gspread, pyautogui, pygetwindow; from rapidocr_onnxruntime import RapidOCR; import google.oauth2.service_account; import core.ocr, core.badge_detector, core.capture, db.database; import PyQt6.QtWidgets; import ui.main_window; assert os.path.exists('main.py'); assert os.path.isdir('assets/badges'); print('smoke-test OK')"
$smokeCode = Invoke-Logged -FilePath $VenvPython -Arguments @("-c", $smoke) -AllowFailure
if ($smokeCode -ne 0) {
    Write-Log "If the traceback mentions onnxruntime DLL load failure, install Microsoft Visual C++ Redistributable 2015-2022 x64 and rerun setup.bat."
    Fail 70 "Smoke-test failed. Installation completed partially, but the app may not start."
}
Write-Log "  Smoke-test OK."

Write-Log ""
Write-Log "============================================================"
Write-Log "  Setup complete!"
Write-Log "============================================================"
Write-Log ""
Write-Log "To run the app:"
Write-Log "  run.bat"
Write-Log ""
Write-Log "First launch:"
Write-Log "  1. Create a season with the Seasons button."
Write-Log "  2. Calibrate capture zones with Calibration."
Write-Log ""
Write-Log "Google Sheets:"
Write-Log "  Put credentials.json in the project root or select it in Settings."
Write-Log ""
Write-Log "Log file:"
Write-Log "  $LogFile"

exit 0
