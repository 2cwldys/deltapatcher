@echo off
rem DeltaPatcher's command line, straight from this repository (Windows).
rem   cli\deltapatcher.bat <command> [options]      for example:  cli\deltapatcher.bat update D:\Game --source https://game.example.com/
rem   cli\deltapatcher.bat                          the help (double-clicked: then a prompt for commands)
rem Uses the built program (dist\deltapatcher-cli.exe) when there is one, otherwise Python 3.8 or later
rem from source. Add this folder to PATH to type just "deltapatcher".
setlocal
set "ROOT=%~dp0.."
rem Double-clicked: this cmd window was opened just for this file, not a terminal someone types in. The
rem program then keeps the window open: a prompt with no command, Enter after one (--close: no waiting).
echo "%cmdcmdline%" | "%SystemRoot%\System32\find.exe" /i "%~nx0" >nul && set "DELTAPATCHER_WINDOW=1"

if not exist "%ROOT%\dist\deltapatcher-cli.exe" goto :source
"%ROOT%\dist\deltapatcher-cli.exe" %*
exit /b %ERRORLEVEL%

:source
set "PYTHONPATH=%ROOT%;%PYTHONPATH%"
where py >nul 2>nul
if errorlevel 1 goto :python
py -3 -m deltapatcher %*
exit /b %ERRORLEVEL%

:python
where python >nul 2>nul
if errorlevel 1 goto :none
python -m deltapatcher %*
exit /b %ERRORLEVEL%

:none
echo DeltaPatcher needs Python 3.8 or later (https://www.python.org), or the built dist\deltapatcher-cli.exe (build.ps1). 1>&2
if defined DELTAPATCHER_WINDOW pause
exit /b 1
