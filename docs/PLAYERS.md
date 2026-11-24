# For players: keeping a game folder up to date

The game's maker gives you two things: **DeltaPatcher** (`DeltaPatcher.exe`) and a **source
address**, for example `https://game.example.com/`.

## First time

1. Start `DeltaPatcher.exe`. It opens on the **Update** tab.
2. *Source*: paste the address the game's maker gave you.
3. *Folder*: the game folder, or an empty folder for a fresh install. Use **Browse** to pick it.
4. Click **Update**. The bar shows the download; the log says what was installed.

DeltaPatcher remembers both fields, so next time you only click **Update**.

## Every time after

Click **Update** with the game closed. Only files that changed since your last update are downloaded,
so a small patch is a small download even when the game is large.

- **Check** tells you what an update would change, without changing anything.
- **Roll back** puts back exactly what the last update replaced or removed, if a new release causes a
  problem.
- **Stop** cancels a download. Nothing in your folder changes until every file has arrived and been
  checked.

## What it will and will not touch

- It never deletes files you added yourself, or files that were in the folder before DeltaPatcher
  first installed them.
- Files the game's maker marked as *protected* (usually your settings, such as `config/*.cfg`) are
  created when missing and never overwritten after that.
- It checks every downloaded file against its SHA-256 before using it. A damaged or incomplete download
  is never installed.
- If an update is interrupted (crash, power cut), the next update undoes the half-finished one first,
  so the folder is never left half old, half new.
- It keeps its own notes in a hidden `.deltapatcher` folder inside the game folder. Leave it there: it is
  how DeltaPatcher knows which files it installed, and it holds the copies **Roll back** uses.

## The tray icon

While DeltaPatcher is open, its icon sits in the notification area (tray). Right-click it to show the
window or quit.

## If something goes wrong

- **"another update or rollback is running in this folder"**: another DeltaPatcher window is updating
  the same folder. Wait for it, or close it.
- **The update failed and says a file is in use**: close the game and try again. The failed update
  was already undone, so your folder is as it was.
- **"plain http to another machine"** warning: the address starts with `http://`, not `https://`. Ask
  the game's maker for an `https://` address. On a home network you trust, it is fine to continue.

More messages are explained in [Troubleshooting](TROUBLESHOOTING.md).
