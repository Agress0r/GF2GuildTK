param([string]$PythonExe = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not $PythonExe) { $PythonExe = Join-Path $PSScriptRoot 'GF2TTK\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Run setup.bat first: GF2TTK environment is missing.'
}
$pythonExe = (Resolve-Path -LiteralPath $PythonExe).Path

& $pythonExe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }

& $pythonExe -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    & $pythonExe -m pip install 'pyinstaller>=6.10,<7'
    if ($LASTEXITCODE -ne 0) { throw 'Could not install PyInstaller.' }
}

& $pythonExe -m PyInstaller --noconfirm --clean --distpath dist `
    --workpath 'build\pyinstaller' 'packaging\portable.spec'
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }

$exe = Join-Path $PSScriptRoot 'dist\GuildTracker.exe'
& $pythonExe scripts\report_bundle.py $exe (Join-Path $PSScriptRoot 'build\bundle_report.md')
if ($LASTEXITCODE -ne 0) { throw 'Bundle analysis failed.' }
Write-Host "Portable EXE: $exe"
Get-Item -LiteralPath $exe | Select-Object Name, Length
