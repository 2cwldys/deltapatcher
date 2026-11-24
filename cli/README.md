# The command line

Two ways to run DeltaPatcher's command line:

| | |
| --- | --- |
| **The built program** | `deltapatcher-cli.exe` (Windows) or `deltapatcher-cli` (Linux, macOS), made by `build.ps1` / `build.sh` into `dist/`. It is a console program: run it in a terminal. |
| **These scripts** | `cli\deltapatcher.bat` (Windows) and `cli/deltapatcher.sh` (Linux, macOS, Git Bash). They run the built program when `dist/` has one, otherwise DeltaPatcher from source with Python 3.8 or later. Nothing to build. |

```
cli\deltapatcher.bat --help
cli\deltapatcher.bat publish D:\MyGame D:\store --name "My Game" --version 1.0 --sign
cli\deltapatcher.bat update D:\Players\MyGame --source https://game.example.com/

cli/deltapatcher.sh update ~/games/mygame --source https://game.example.com/
```

To type just `deltapatcher` anywhere:
- Windows: add this `cli` folder to your PATH (Settings > System > About > Advanced system settings >
  Environment Variables > Path > New).
- Linux, macOS: `ln -s "$PWD/cli/deltapatcher.sh" ~/.local/bin/deltapatcher`

Every command, option and exit code: [docs/CLI.md](../docs/CLI.md).
