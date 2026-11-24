# Quick start: try it on your own PC

Five minutes, nothing to set up. You will publish a folder, serve it from your PC, update a second
folder from it, and watch a change travel on its own.

## With the app

1. Start DeltaPatcher (from source: `cd app`, `npm ci`, `npm run dev`).
2. Make two empty folders, for example `D:\DemoRelease` and `D:\DemoGame`. Put a few files in
   `D:\DemoRelease`: anything works (a `.zip`, a `.pk3`, pictures, text files).
3. **Publish** page:
   - *Folder to release*: `D:\DemoRelease`
   - *Store*: `D:\DemoStore` (it is created for you)
   - *Name* and *Version*: anything, for example `Demo` and `1`
   - Click **Publish**. The result card shows how many files and blobs were written.
4. Under **Try it here**, click **Serve locally**. It says the store is at `http://localhost:8000/`.
5. **Update** page:
   - *Source*: `http://localhost:8000/`
   - *Folder*: `D:\DemoGame`
   - Click **Check**: it shows what would be downloaded. Click **Update**: it downloads it.
6. Change one file in `D:\DemoRelease`, set *Version* to `2` on the Publish page and click **Publish**
   again (the local server keeps running). Back on **Update**, click **Check**: only the changed file is
   counted. **Update** fetches just that file.
7. **Roll back** puts back the files the last update replaced.

Everything you type is saved in `deltapatcher.ini` (the **Settings** page), so it is there next time.
**Console** at the bottom shows every step; **Logs** opens the log files.

## With the command line

```
deltapatcher publish D:\DemoRelease D:\DemoStore --name Demo --version 1
deltapatcher serve D:\DemoStore
```

Leave `serve` running and open a second terminal:

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
