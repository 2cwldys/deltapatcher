# Security

DeltaPatcher moves files onto people's computers and logs in to servers with SSH keys, so it is
built to fail closed. This page says what it protects, how, what it leaves to you, and who answers for
what it installs.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: the repository's
**Security** tab > **Report a vulnerability**. Include what you found, how to reproduce it, and the
version (`deltapatcher --version`). You will get an answer, and credit if you want it, once a fix is out.

Only the latest release gets security fixes.

## What it protects

| What | How |
| --- | --- |
| **Who made a release** | Releases can be signed with an Ed25519 key (`deltapatcher sign keygen`, then publish with `--sign`). A client checks the signature against the publisher key it was given (`--trust-key`, or the window's *Publisher key*), or against the key of the first signed release it saw, which it then pins. After that, a release signed by another key, or not signed at all, is refused. The signature file is named by the manifest's SHA-256, so it can never be paired with a different manifest, even halfway through a deploy. |
| **Every file a client installs** | Each blob is checked against the manifest's SHA-256 and size before anything in the folder changes. A damaged, truncated or swapped file is never installed. |
| **The client's folder** | The manifest is treated as untrusted input. Paths that could leave the folder (`..`, absolute paths, drive letters, links or junctions on the way), Windows device names (`CON`, `con .txt`, `COM1`), names Windows cannot store, and the client's own `.deltapatcher` folder are refused before anything is written. Updates are journalled and undone on failure; files the client did not install are never removed. |
| **Private SSH keys** | Read only by OpenSSH. DeltaPatcher never opens one; it asks `ssh-keygen` whether a key has a passphrase. New keys are written once, readable by your account only (`icacls` on Windows, mode 600 elsewhere), and an existing key is never overwritten. |
| **Passphrases** | Never stored, logged, written to a settings file, or put on a command line. SSH keys are unlocked into `ssh-agent`: on the command line `ssh-add` asks in the terminal; the app asks in its own dialog and hands the passphrase to `ssh-add` through a one-time, token-checked connection on 127.0.0.1, never a command line, the environment or a file. A signing key's passphrase is asked for each publish and dropped after. |
| **The app** | The window has no Node.js access (context isolation and the Chromium sandbox). It can ask the main process to run only a fixed list of engine commands, it never navigates away, and links open in the browser. Passphrases reach the engine on its standard input. |
| **Servers** | Host keys are shown and confirmed the first time, then checked strictly on every connection (`StrictHostKeyChecking=yes`, DeltaPatcher's own `known_hosts`). A changed host key stops the deploy. |
| **Server access** | Deploys need only SFTP, so the key's `authorized_keys` line limits it to SFTP: `restrict,command="internal-sftp"` (no shell, no forwarding). |
| **A server's password** | *Put key on server* (`ssh install-key`) logs in once with the server user's password to add your key's line. The password is asked for in the app's dialog or the terminal, reaches the engine on its standard input, is sent only after the server shows the host key you already trusted (an unknown or changed host gets nothing), is used for that one login, and is never stored, logged or put on a command line. |
| **DeltaPatcher's own updates** | The newest release is asked of the project's GitHub releases. A downloaded file is used only if its size and SHA-256 match what GitHub gives for it, and nothing is downloaded without asking. The app's main process runs only a file the engine downloaded into its own update folder with a release's name. |
| **Deploys** | Blobs go up first, then the signature, then the manifest, whose size is checked before it is switched in. A deploy that fails part way leaves the server serving the release it served before. Blobs and signatures a live manifest names are never removed. |
| **What a web server shows** | The built-in server answers only `manifest.json`, `manifest.previous.json`, `blobs/<sha256>`, `sigs/<sha256>.sig` and, for a release shared by torrent, `release.torrent` and `release.item` (also under the torrent's name): no listings, no other files. Published stores get a `.htaccess` that makes Apache do the same. |
| **Access tokens** | A header given with `--header` (an `Authorization` token, say) is sent only to the host it was given for, never after a redirect to another host. A redirect from `https` to `http` is refused. Header values never reach the log. |
| **Settings and logs** | Settings (`deltapatcher.ini`) hold addresses, folder paths, public keys and the *paths* of key files, never a key or a passphrase. Logs hold what was shown on screen, with header values hidden. |
| **Players, in a publisher's statistics** | Statistics keep counts only. A web server's access log is read where it is or from a temporary copy that is deleted as soon as it is read; players are told apart in memory, by a salted hash of their address that exists only while the log is read; no address, user agent or log line is stored, logged or sent anywhere. An updater tells the server only which DeltaPatcher and which release it has, in its User-Agent. |
| **Backups and restores** | A backup only reads the live release where it is sent, and checks every file against its SHA-256 before keeping it; a damaged file means no backup. A restore asks first, sends its files before its manifest, and removes nothing at the server or folder. |
| **Force stop** | The app's red **Force stop** ends every job it runs at once and starts none until it is lifted, also after a restart. An update stopped part way is undone the next time that folder is updated; a release stopped before its manifest went up never reaches players. |
| **Torrents** | Off unless a publisher turns them on for a project, and refused completely with one setting (*Torrents*: `refuse`; in the settings file anything but `allow` refuses, and so does a settings file that cannot be read): then DeltaPatcher makes no BitTorrent connection at all, whatever a project file asks. Only signed releases can use them. Everything from a peer is checked the way a download from a server is: the release's signature against the publisher key you trust, then every file against its SHA-256, before anything in your folder changes; a peer that sends bad data is banned. DeltaPatcher uses no tracker, never contacts the public DHT's routers itself, and asks the router for nothing unless you turn on port mapping. |
| **Seeding** | Only with your yes, per project, and only while DeltaPatcher runs. It offers only files DeltaPatcher has hashed itself against the newest release it knows: a file you changed, a file the release keeps as yours, or a file of an older release is never offered. Seeding pauses while an update changes the folder. |
| **The repository** | `.gitignore` refuses key files, `known_hosts`, `.env`, settings files, stores, builds and virtual environments. |

## What it leaves to you

- **Use HTTPS for the source.** Without a signature, the manifest is only as trustworthy as the
  connection it came over: on plain `http` across a network it could be altered on the way (the window
  warns about it). Signing your releases closes that gap; HTTPS on top keeps players' downloads private.
- **Keep your signing key safe.** Whoever has it, and its passphrase, can publish releases your players
  will accept. Keep it off build servers you do not control, give it a passphrase, and back it up. If
  it leaks, make a new one and give players the new public key: their updater refuses releases signed
  by the old one only after they trust the new one (`--trust-key`).
- **First contact is trust on first use.** A player who was not given your publisher key pins the key of
  the first signed release they download. Ship the key with your game or launcher (`--trust-key`, or a
  `deltapatcher.ini` beside the program with `trust_key` filled in) to close that window.
- **Compare host key fingerprints for real.** Confirming a server's key without checking it defeats the
  check.
- **Keys without a passphrase** (for build servers) work for anyone who copies the file. Limit them to
  SFTP and to one deploy user that owns only the web folder.
- **Antivirus reputation checks** may flag new, unsigned builds of the programs. Signing the programs
  themselves (Authenticode) is up to whoever distributes them.

## Torrents: the law and your privacy

- **BitTorrent is a lawful protocol; sharing what you have no right to share is not.** A publisher who turns
  torrents on answers for what the release holds and for the right to have it shared peer to peer. A player
  who seeds shares those files with others and answers for doing so where they live. When unsure, do not seed.
- **Peers see your address.** Downloading or seeding by BitTorrent shows your IP address to the peers you
  connect to, and to DHT nodes for projects with no address. Protocol encryption hides what the traffic
  looks like, not who you are. The DHT protocol cannot be closed: DeltaPatcher never contacts the public
  DHT's routers itself, but an outside BitTorrent node that reaches a DeltaPatcher can join it.
- **Your router stays as it is.** DeltaPatcher asks no router to open a port unless you turn on *Port
  mapping* (UPnP/NAT-PMP). Without it, others behind routers cannot connect to you; you still download and
  upload through the connections you make.
- **The firewall.** Windows, and macOS when its firewall is on, ask whether DeltaPatcher may accept
  connections the first time it seeds. Allowing it lets other players reach you; refusing it still lets you
  download. On Linux, a firewall you run (ufw, firewalld) needs the torrent port opened by hand.
- **No warranty.** DeltaPatcher is provided under the MIT licence "as is", without warranty of any kind; its
  authors and contributors are not liable for any damages, nor for what anyone downloads, seeds or
  distributes with it.

## Who answers for what is installed

DeltaPatcher is a tool for moving files. It installs exactly what a publisher released, checked byte by
byte against that publisher's manifest, and nothing else. It never looks inside a file, so it cannot tell
whether a release is safe, lawful, properly licensed, or what anyone expected.

- **A publisher answers for what they release**: the files, the right to share them, and what they do on
  the computers they reach. DeltaPatcher only carries them.
- **A player chooses whom to trust.** Adding a source address, a publisher key or a project preset lets that
  publisher put files on your computer. Add only publishers you trust, as you would before running a
  program they sent you. A signature proves who released something, not that it is harmless.
- **Nobody reviews releases.** DeltaPatcher has no central server, store or approval step: anyone can host a
  release, and DeltaPatcher's authors never see, check or control what is published or installed with it.
- **DeltaPatcher's authors and contributors are not responsible** for what anyone releases, hosts or
  installs with it, nor for any harm, loss or damage that follows from it. DeltaPatcher is provided "as is",
  without warranty of any kind, under the MIT licence; the [LICENSE](LICENSE) file holds the binding terms.

A harmful release is a matter for its publisher and whoever hosts it: DeltaPatcher's authors cannot reach
or remove it. A security problem in DeltaPatcher itself goes to the private report described at the top of
this page.
