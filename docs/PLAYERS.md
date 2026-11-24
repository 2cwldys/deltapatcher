# For players: keeping a game folder up to date

The game's maker gives you two things: **the DeltaPatcher app** and a **source
address**, for example `https://game.example.com/`.

## First time

1. Start DeltaPatcher. It opens on the **Update** page.
2. *Source*: paste the address the game's maker gave you.
3. *Folder*: the game folder, or an empty folder for a fresh install. Use **Browse** to pick it.
4. *Publisher key*: if the game's maker gave you one (it starts with `ed25519:`), paste it. Then only
   releases they signed are installed. Leave it empty otherwise.
5. Click **Update**. The bar shows the download; the log says what was installed.

DeltaPatcher remembers these fields, so next time you only click **Update**. If the game came with a
`deltapatcher.ini` beside the DeltaPatcher program, they are already filled in.

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
- Files the game's maker marked as *protected* (usually your settings and saves) are
  created when missing and never overwritten after that.
- It checks every downloaded file against its SHA-256 before using it. A damaged or incomplete download
  is never installed.
- If an update is interrupted (crash, power cut), the next update undoes the half-finished one first,
  so the folder is never left half old, half new.
- If the release is signed, DeltaPatcher checks the signature before anything else. Once it has
  installed a release signed by a key, it refuses releases signed by any other key, and unsigned ones,
  unless you change the *Publisher key* yourself.
- It keeps its own notes in a hidden `.deltapatcher` folder inside the game folder. Leave it there: it is
  how DeltaPatcher knows which files it installed, and it holds the copies **Roll back** uses. Its
  `update.log` lists every file each update added, replaced or removed.

**Logs** (bottom right of the window) opens DeltaPatcher's own log, everything it showed with the time:
useful when asking the game's maker for help. The **Settings** page shows `deltapatcher.ini`, every
setting with a line explaining it.

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
