@echo off
rem DeltaPatcher's command line, straight from this repository (Windows).
rem   cli\deltapatcher.bat <command> [options]      for example:  cli\deltapatcher.bat update D:\Game --source https://game.example.com/
rem   cli\deltapatcher.bat                          the help
rem Uses the built program (dist\deltapatcher-cli.exe) when there is one, otherwise Python 3.8 or later
rem from source. Add this folder to PATH to type just "deltapatcher".
setlocal
set "ROOT=%~dp0.."
set "DPHELP="
if "%~1"=="" set "DPHELP=--help"

if exist "%ROOT%\dist\deltapatcher-cli.exe" (
    "%ROOT%\dist\deltapatcher-cli.exe" %* %DPHELP%
    exit /b %ERRORLEVEL%
)

set "PYTHONPATH=%ROOT%;%PYTHONPATH%"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m deltapatcher %* %DPHELP%
    exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if not errorlevel 1 (
    python -m deltapatcher %* %DPHELP%
    exit /b %ERRORLEVEL%
)
echo DeltaPatcher needs Python 3.8 or later (https://www.python.org), or the built dist\deltapatcher-cli.exe (build.ps1). 1>&2
exit /b 1
