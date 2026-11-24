<#
  Builds dist\DeltaPatcher.exe (the window) and dist\deltapatcher-cli.exe (the command line).
  Uses a virtual environment in .venv so only what DeltaPatcher needs goes into the programs.

    powershell -ExecutionPolicy Bypass -File build.ps1           build
    powershell -ExecutionPolicy Bypass -File build.ps1 -Test     run the tests first
#>
param([switch]$Test)
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
    & $py -m unittest discover -s tests -v
    if ($LASTEXITCODE) { throw 'tests failed' }
}

Write-Host '== icon'
& $py tools\make_icon.py
if ($LASTEXITCODE) { throw 'make_icon.py failed' }

Write-Host '== pyinstaller'
& $py -m PyInstaller packaging\deltapatcher.spec --noconfirm --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE) { throw 'pyinstaller failed' }

Write-Host '== self-test of the built window'
$report = Join-Path $PSScriptRoot 'build\selftest.json'
Remove-Item $report -ErrorAction SilentlyContinue
$p = Start-Process -FilePath dist\DeltaPatcher.exe -ArgumentList '--selftest', "`"$report`"" -PassThru -Wait
Get-Content $report
if ($p.ExitCode) { throw 'the built window failed its self-test' }

Get-ChildItem dist\*.exe | ForEach-Object { '{0}  {1:N1} MB' -f $_.Name, ($_.Length / 1MB) }
