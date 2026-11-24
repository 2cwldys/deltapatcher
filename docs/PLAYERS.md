# For players: keeping a game folder up to date

The game's maker gives you **the DeltaPatcher app**, and either a **project file** (`.deltapatch` or
`.html`) or a **source address**, for example `https://game.example.com/` or `http://203.0.113.10/`.

## First time, with a project file

The easiest way: the file fills in everything for you.

1. Double-click the `.deltapatch` file, or drag either file onto DeltaPatcher's window. (Or, on the
   **Projects** page, **Add a project file...** and pick it.)
2. DeltaPatcher shows what the file holds and who signed it. Tick the projects you want, and pick the
   folder each one keeps up to date: the game's folder, or an empty folder for a fresh install.
3. Click **Add**. With **Update them now** on, they download straight away.

You pick each folder once: DeltaPatcher remembers it, and every update after that, Update all included,
uses it. A project file never holds a folder (a folder on the publisher's computer means nothing on yours),
but the publisher can say where a project goes: the dialog then shows that line above the folder picker,
and adds the folder name they gave to the folder you pick (for example your game's folder, then Mods).

The address and the publisher key come from the file, so even the first download is checked against the
publisher's key. A file changed after it was signed is refused. An unsigned file can be added, with a
warning: add it only if you trust where it came from.

## First time, with an address

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

## Several games

Each game, mod or server you keep up to date is a project, with its own source, folder and publisher key.
The first one is called Default.

- **Add one**: from a project file, as above; or the project picker at the top left > **New project...**,
  give it a name, then fill in its *Source*, *Folder* and *Publisher key* on the **Update** page.
- **Switch**: pick it in the project picker (with many projects, type to search). The Update, Release and
  Settings pages then show that project, its name in their heading.
- **All at once**: the **Projects** page lists every project with its state, such as "Up to date" or
  "3 new, 1 changed, 700 KB to download"; a filter finds one in a long list. **Check all** runs by itself
  when DeltaPatcher starts. **Update all** brings every project up to date, three at the same time (the
  setting *parallel_updates*). One project failing never stops the others, and **Stop** stops them all,
  each folder as it was.
- **Remove one**: in the project picker (point at it), on the **Settings** page (**Remove this
  project...**), or in the **...** menu of its row on the Projects page. **Remove all**, in the **...** menu
  at the top of the Projects page, removes every project but Default. Their settings go; their folders and files always stay.

## Updates while you play

While DeltaPatcher runs, it checks every project every hour, and **Projects** shows how many have an
update waiting. When it is hidden in the tray, a desktop notification says so. These settings are on the
**Settings** page:

- *check_every_hours*: how often it checks (1 by default; 0: only when it starts).
- *auto_update*: projects with an update are updated by themselves, and a notification says what was
  done. Off by default.
- *minimize_to_tray*: minimizing hides the window to the tray, off the taskbar. Off by default.
- *start_with_system*: DeltaPatcher starts with the computer, straight into the tray. Off by default.

## Starting over

**Reset DeltaPatcher**, at the bottom of the **Settings** page, puts DeltaPatcher back as it was just
installed: every project removed, and the settings back to their defaults. Your game folders and their files
stay exactly as they are, and a copy of the old settings is kept beside them
(`settings-before-reset-...`). Adding a project back later finds its files, checks them, and downloads only
what differs.

## When the publisher changes a project

A project added from an **update capable** project file (the mark in the import dialog) follows its
publisher. When they change its description, website, where it goes, its address or its publisher key, a
check shows **The publisher changed this project** on its row and on the Update page:

- **Apply** takes what they changed. Your folder, the name you gave the project and your own settings never
  change.
- **Not now** puts it off until the publisher changes the project again.
- A new address or publisher key is taken only when it is signed with the key you trust now; without a key,
  you are warned more strongly.
- Opening a newer project file for a project you have updates it, instead of adding a second one
  (**Add it as a new project instead** is there too).

**What changed**, beside a check's result, lists the files a release adds, changes and removes, and the
publisher's notes, when the publisher shows file names. The setting *Ask me when the publisher changes this
project* (Settings) turns the questions off for that project.

## Downloading from other players

A publisher can share a project by torrent. DeltaPatcher then downloads from other players as well as from
the publisher's address, or only from players for a project with no address. Everything from a player is
checked as a download from a server is: the publisher's signature, then every file's SHA-256.

- **Seeding** shares the project's files with other players while DeltaPatcher seeds. It is off unless you
  say yes (*Seed this project*, Settings). Only files DeltaPatcher has checked are offered: never a file you
  changed, never your own files the release keeps for you. Seeding pauses while an update changes the folder.
- **Refusing torrents completely**: set *Torrents* to *refuse* (Settings). DeltaPatcher then never uses
  BitTorrent, whatever a project file or its publisher says: a project with an address updates from it
  only, and a project shared by torrent only cannot update.
- **Other players see your IP address** while you download from them or seed to them.
- **Your firewall may ask** whether DeltaPatcher may accept connections, the first time it seeds: Windows
  does, and macOS does when its firewall is on. Allow it to seed well; refusing still lets you download. On
  Linux, open the *Torrent port* in your firewall (ufw, firewalld) if you run one.
- **Your router**: others reach you through the *Torrent port* (6881) when it is forwarded to your computer.
  DeltaPatcher asks the router to do that only with *Port mapping* on.
- **On a Mac**, torrents need macOS 14 or later; older ones update from the address.

The law and your privacy: [SECURITY.md](../SECURITY.md#torrents-the-law-and-your-privacy).

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

## Stopping everything at once

**Force stop**, the red box at the bottom left, ends whatever DeltaPatcher is doing, for every project:

- A download stops and the folder stays as it was. An update that was already putting files in place is
  undone the next time that folder is updated.
- The automatic checks, and updates by themselves, wait.
- The pages are greyed out until you click **Lift the stop**, and DeltaPatcher stays stopped after a
  restart too.

Lifting it starts nothing that was stopped: update again when you are ready. The automatic checks come
back on their usual schedule, counted from when you lifted it.

## The tray icon

While DeltaPatcher is open, its icon sits in the notification area (tray). Right-click it to show the
window or quit. With *minimize_to_tray* on, minimizing the window hides it there; a click on the icon
brings it back.

## If something goes wrong

- **"another update or rollback is running in this folder"**: another DeltaPatcher window is updating
  the same folder. Wait for it, or close it.
- **The update failed and says a file is in use**: close the game and try again. The failed update
  was already undone, so your folder is as it was.
- **"plain http to another machine"** warning: the address starts with `http://`, not `https://`. That
  is fine with the game's publisher key in *Publisher key*: only releases they signed are installed. Without
  a key, ask the game's maker for it, or for an `https://` address. On a home network you trust, it is fine
  to continue.

More messages are explained in [Troubleshooting](TROUBLESHOOTING.md).
