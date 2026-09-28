param([string]$PythonExe = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $PythonExe) { $PythonExe = Join-Path $PSScriptRoot 'GF2TTK\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Specify a Python 3.12 x64 interpreter with -PythonExe, or run setup.bat.' }
& $PythonExe -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required.' }
$releasePython = Join-Path $PSScriptRoot '.venv-release\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $releasePython)) {
    & $PythonExe -m venv (Join-Path $PSScriptRoot '.venv-release')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the release environment.' }
}
& $releasePython -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8"
if ($LASTEXITCODE -ne 0) { throw 'The release environment must use Python 3.12 x64.' }
& $releasePython -m pip install --disable-pip-version-check -r requirements-release.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install release dependencies.' }
& $releasePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Broken release dependencies.' }
& $releasePython scripts\check_release.py
if ($LASTEXITCODE -ne 0) { throw 'Source manifest check failed.' }
$previousQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $releasePython -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; release was not built.' }
} finally { $env:QT_QPA_PLATFORM = $previousQtPlatform }
& (Join-Path $PSScriptRoot 'build.ps1') -PythonExe $releasePython
& $releasePython scripts\prepare_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging or packaged smoke test failed.' }
