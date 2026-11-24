# Quick start: try it on your own PC

Five minutes, nothing to set up. You will release a folder on your own PC, update a second folder from
it, and watch a change travel on its own.

## With the app

1. Start DeltaPatcher.
2. Make two folders, for example `D:\DemoRelease` and `D:\DemoGame`. Put a few files in
   `D:\DemoRelease`: anything works (a `.zip`, a `.pk3`, pictures, text files).
3. **Release** page:
   - **1. What to release**: *Folder to release* `D:\DemoRelease`; *Name* and *Version* anything, for
     example `Demo` and `1`.
   - **2. Where players download it from**: **This computer, to test**.
   - **3. Release**: click **Release**. It publishes the folder and serves it at `http://localhost:8000/`.
4. **Update** page:
   - *Source*: `http://localhost:8000/`
   - *Folder*: `D:\DemoGame`
   - Click **Check**: it shows what would be downloaded. Click **Update**: it downloads it.
5. Change one file in `D:\DemoRelease` and click **Release** again (with version `2`, or `1` again: either
   works). Back on **Update**, click **Check**: only the changed file is counted. **Update** fetches just
   that file.
6. **Roll back** puts back the files the last update replaced.

Everything you type is saved in `deltapatcher.ini` (the **Settings** page), so it is there next time.
**Console** at the bottom shows every step; **Logs** opens the log files.

When it works, pick **Your server (SSH)** in step 2 to send releases to a VPS instead: see
[Setting up a VPS](VPS-SETUP.md).

## With the command line

Double-click the command line on Windows for a prompt, or use a terminal:

```
deltapatcher release D:\DemoRelease --version 1 --to local
```

That publishes the folder and serves it at `http://localhost:8000/` until you press Ctrl+C. In a second
terminal:

```
deltapatcher update D:\DemoGame --source http://localhost:8000/
deltapatcher update D:\DemoGame --check        # what would change; changes nothing
deltapatcher rollback D:\DemoGame              # undo the last update
```

`deltapatcher` is `deltapatcher-cli` from a release, `cli\deltapatcher.bat` / `cli/deltapatcher.sh`
in this repository, or `python -m deltapatcher` from source.

## What next

- To release to other people, and to sign your releases, see [Publishing a release](PUBLISHING.md).
- To host it on the internet, see [Setting up a VPS](VPS-SETUP.md).
