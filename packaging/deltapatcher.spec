# PyInstaller spec: the engine, built two ways from one analysis.
#   dist/deltapatcher-cli(.exe)    the command line: one self-contained file to copy anywhere
#   dist/engine/                   the engine the DeltaPatcher app (app/) runs: deltapatcher-engine(.exe)
#                                  and its _internal folder. A folder starts in a fraction of the time
#                                  a one-file program takes, which unpacks itself on every run.
# The two programs need different names: PyInstaller keeps each one's parts under its name in the work folder.
# Build:  pyinstaller packaging/deltapatcher.spec --noconfirm   (build.ps1 / build.sh do it in a venv)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "deltapatcher", "assets", "deltapatcher.ico")
EXCLUDES = ["numpy", "scipy", "pandas", "matplotlib", "torch", "IPython", "pytest", "tkinter", "PIL"]
OPTIONS = dict(console=True, icon=ICON if os.name == "nt" else None, upx=False, strip=False, debug=False)

a = Analysis([os.path.join(SPECPATH, "entry_cli.py")], pathex=[ROOT], excludes=EXCLUDES, noarchive=False)
pyz = PYZ(a.pure)

EXE(pyz, a.scripts, a.binaries, a.datas, [], name="deltapatcher-cli", runtime_tmpdir=None, **OPTIONS)

engine = EXE(pyz, a.scripts, [], exclude_binaries=True, name="deltapatcher-engine", **OPTIONS)
COLLECT(engine, a.binaries, a.datas, name="engine", upx=False, strip=False)
