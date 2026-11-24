# PyInstaller spec: two single-file programs from one source tree.
#   dist/DeltaPatcher.exe       the window (no console), the .ico on the file, the window and the tray
#   dist/deltapatcher-cli.exe   the command line
# Build:  pyinstaller packaging/deltapatcher.spec --noconfirm   (build.ps1 / build.sh do it in a venv)
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "deltapatcher", "assets", "deltapatcher.ico")
DATAS = [(os.path.join(ROOT, "deltapatcher", "assets"), os.path.join("deltapatcher", "assets"))]
HIDDEN = collect_submodules("pystray") + ["PIL.IcoImagePlugin", "PIL.PngImagePlugin"]
EXCLUDES = ["numpy", "scipy", "pandas", "matplotlib", "torch", "IPython", "pytest"]


def analysis(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[ROOT], datas=DATAS, hiddenimports=HIDDEN,
                    excludes=EXCLUDES, noarchive=False)


def onefile(a, name, console):
    pyz = PYZ(a.pure)
    return EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, console=console, icon=ICON,
               upx=False, strip=False, debug=False, runtime_tmpdir=None)


gui = analysis("entry_gui.py")
cli = analysis("entry_cli.py")
onefile(gui, "DeltaPatcher", console=False)
onefile(cli, "deltapatcher-cli", console=True)
