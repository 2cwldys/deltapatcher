<#
  Builds DeltaPatcher on Windows:
    1. the engine: dist\deltapatcher-cli.exe, the command line in one file, and dist\engine\, the same
       engine as a folder for the app (PyInstaller, self-contained: no Python needed to run either)
    2. the app:    dist\app\DeltaPatcher-<version>-portable.exe and -setup.exe (Electron, with the engine inside)

    powershell -ExecutionPolicy Bypass -File build.ps1            both
    powershell -ExecutionPolicy Bypass -File build.ps1 -Test      run the tests first
    powershell -ExecutionPolicy Bypass -File build.ps1 -EngineOnly

  Needs Python 3.8+ (the py launcher) and Node.js 20+ with npm. Python packages go into .venv,
  Node packages into app\node_modules (from app\package-lock.json): nothing is installed system-wide.
#>
param([switch]$Test, [switch]$EngineOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host '== making .venv'
    py -3 -m venv .venv
}
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $py -m pip install --disable-pip-version-check -q -r requirements-build.txt
if ($LASTEXITCODE) { throw 'pip install failed' }

if ($Test) {
    Write-Host '== tests'
    & $py -m unittest discover -s tests
    if ($LASTEXITCODE) { throw 'tests failed' }
}

Write-Host '== icon'
& $py tools\make_icon.py
if ($LASTEXITCODE) { throw 'make_icon.py failed' }

Write-Host '== engine (PyInstaller)'
& $py -m PyInstaller packaging\deltapatcher.spec --noconfirm --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE) { throw 'pyinstaller failed' }
$v = & dist\deltapatcher-cli.exe --json --no-log version | ConvertFrom-Json
if (-not $v.ok) { throw 'the built engine does not answer' }
Write-Host ("   engine {0} answers" -f $v.data.version)
$v = & dist\engine\deltapatcher-engine.exe --json --no-log version | ConvertFrom-Json
if (-not $v.ok) { throw 'the built engine folder does not answer' }
if ($EngineOnly) { return }

Write-Host '== app (Electron)'
Push-Location app
try {
    if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund; if ($LASTEXITCODE) { throw 'npm ci failed' } }
    npm run typecheck
    if ($LASTEXITCODE) { throw 'typecheck failed' }
    npm run dist
    if ($LASTEXITCODE) { throw 'electron-builder failed' }
} finally {
    Pop-Location
}
Get-ChildItem dist\deltapatcher-cli.exe, dist\app\*.exe | ForEach-Object { '{0}  {1:N1} MB' -f $_.Name, ($_.Length / 1MB) }
