<#
  Builds DeltaPatcher on Windows:
    1. the engine: dist\deltapatcher-cli.exe, the command line in one file, and dist\engine\, the same
       engine as a folder for the app (PyInstaller, self-contained: no Python needed to run either)
    2. the app:    dist\app\DeltaPatcher-<version>-portable.exe and -setup.exe (Electron, with the engine inside)
    3. the release files: dist\release\, both named for a GitHub release
       (dist\ is not tracked by git: upload the files in dist\release\ to a release yourself)

    powershell -ExecutionPolicy Bypass -File build.ps1            all of it
    powershell -ExecutionPolicy Bypass -File build.ps1 -Test      run the tests first
    powershell -ExecutionPolicy Bypass -File build.ps1 -EngineOnly

  Needs Python 3.11 to 3.13 (the py launcher) and Node.js 20+ with npm: the build tools need 3.11, the BitTorrent
  library built into the engine (libtorrent 2.0.15) has no wheel after 3.13, and the engine itself runs on
  Python 3.8 or later. Python packages go into .venv,
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
& $py -c "import sys; sys.exit(sys.version_info < (3, 11))"
if ($LASTEXITCODE) {
    throw 'The build needs Python 3.11 or later (its tools do; the engine itself runs on 3.8+). Install a newer Python, delete .venv and run build.ps1 again.'
}
& $py -c "import sys; sys.exit(sys.version_info >= (3, 14))"
if ($LASTEXITCODE) {
    throw 'The build needs Python 3.11 to 3.13: the BitTorrent library built into the engine (libtorrent 2.0.15) has no wheel for a newer Python. Install Python 3.12, delete .venv and run build.ps1 again.'
}
& $py -m pip install --disable-pip-version-check -q -r requirements-build.txt
if ($LASTEXITCODE) { throw 'pip install failed' }

if ($Test) {
    Write-Host '== tests'
    $env:DP_REQUIRE_TORRENT = '1'          # libtorrent is in .venv: a torrent test that cannot run fails
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
Write-Host '== BitTorrent in the built engine (a session on 127.0.0.1, a small torrent made and checked)'
foreach ($exe in 'dist\deltapatcher-cli.exe', 'dist\engine\deltapatcher-engine.exe') {
    & $exe --no-log version --torrent
    if ($LASTEXITCODE) { throw "the BitTorrent self-test of $exe failed" }
}
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

Write-Host '== release files'
& $py tools\release_files.py
if ($LASTEXITCODE) { throw 'release_files.py failed' }
