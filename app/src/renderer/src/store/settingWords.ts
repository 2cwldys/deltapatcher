// The Settings page in plain words: for each setting of a settings file, its label, one short line under it, and
// whether it is one rarely changed, folded under More in its section. The setting's own name (the one in the
// .ini file) stands small beside the label, and the engine's whole explanation of it is behind the (i).
// Nothing here uses the window, so a test can run it as it is.

export interface SettingWords {
  label: string
  line: string
  // rarely changed: under More in its section
  more?: boolean
}

// the settings the Settings page leaves out: kept by the Projects page and the project picker (the name, the added
// files, the one shown), or by DeltaPatcher itself (the publisher's details last accepted, the change answered Not
// now, when the private project file applied was made)
export const HIDDEN = ['title', 'project_files', 'current_project', 'publisher_accepted', 'publisher_not_now', 'project_file_made']

export const WORDS: Record<string, SettingWords> = {
  // Project
  description: { label: 'Description', line: 'One line about this project, shown with it.' },
  website: { label: 'Website', line: 'Its web page, shown with it.' },
  install_hint: { label: 'Where it goes', line: 'Said to players as they pick its folder.' },
  install_folder: { label: 'Its folder name', line: 'The folder it goes into, inside the one a player picks.', more: true },
  follow_publisher: { label: 'Ask me when the publisher changes this project', line: 'Nothing changes until you apply it.' },
  // Update
  source_url: { label: 'Source', line: 'Where releases come from: an address or a folder.' },
  install_dir: { label: 'Folder', line: 'The folder to keep up to date.' },
  trust_key: { label: 'Publisher key', line: "The publisher's public key (ed25519:...)." },
  workers: { label: 'Downloads at once', line: 'How many files download at the same time.', more: true },
  // Publish
  publish_src: { label: 'Folder to release', line: 'The files exactly as players should have them.' },
  store_dir: { label: 'Where the release is prepared', line: "Empty: DeltaPatcher's own folder.", more: true },
  name: { label: 'Name', line: "The release's name, shown to players." },
  version: { label: 'Version', line: "The release's version, shown to players." },
  protect: { label: 'Players keep their own copy', line: 'Paths players keep once they have them, separated by ;' },
  leave_out: { label: 'Leave out of the release', line: 'Paths players never get, separated by ;' },
  sign: { label: 'Sign each release', line: "Players' updaters then refuse anything not signed by your key." },
  signing_key: { label: 'Signing key', line: "The key file's path, never the key.", more: true },
  signing_public_key: { label: "Signing key's public key", line: 'Releases must be signed with it; set by a private project file.', more: true },
  htaccess: { label: '.htaccess for Apache', line: 'Serve only the release: no listings, the right cache headers.', more: true },
  serve_port: { label: 'Port on this computer', line: 'The port for This computer, to test.', more: true },
  share_updates: { label: 'Players follow this project', line: 'Releases carry its details; players are asked before a change.' },
  show_files: { label: 'Show players the file names', line: 'Which files a release adds, changes and removes.' },
  repack_archives: { label: 'Repack archives so they patch inside', line: '.tar.gz, .gz and solid .7z packed again one file at a time.' },
  // Server
  deploy_mode: { label: 'Where players get it', line: 'sftp (your server), folder, or local (this computer).' },
  ssh_host: { label: 'Host', line: "The server's address." },
  ssh_user: { label: 'User', line: 'The user DeltaPatcher logs in as.' },
  ssh_port: { label: 'Port', line: "The server's SSH port.", more: true },
  ssh_key: { label: 'SSH key file', line: "The key file's path, never the key.", more: true },
  remote_root: { label: 'Remote folder', line: 'The folder on the server that its web server serves.' },
  deploy_folder: { label: 'Copy it to', line: 'The folder a release is copied to (A folder).' },
  public_url: { label: 'Players download from', line: 'The address players update from.' },
  access_log: { label: 'Access log', line: "The web server's access log, for statistics." },
  // Backups
  backup_dir: { label: 'Backup folder', line: 'Empty: beside the main settings file.' },
  backup_every_hours: { label: 'Hours between backups', line: 'The app backs up the live release this often; 0: never.' },
  backup_keep_daily: { label: 'Daily backups kept', line: 'The newest of each of the last this many days.', more: true },
  backup_keep_weekly: { label: 'Weekly backups kept', line: 'The newest of each of the last this many weeks.', more: true },
  backup_keep_monthly: { label: 'Monthly backups kept', line: 'The newest of each of the last this many months.', more: true },
  backup_max_size: { label: 'Size limit', line: 'The most the backups may hold, such as 10 GB; empty: no limit.', more: true },
  // Torrents
  torrent: { label: 'Share by torrent too', line: 'Players also download from each other; needs signed releases.' },
  torrent_id: { label: 'Torrent id', line: 'Made once by DeltaPatcher; players find the release by it.', more: true },
  torrent_name: { label: 'Torrent name', line: 'Empty: the last part of the address, else release.', more: true },
  seeds: { label: 'Seeds', line: 'Computers players connect to first: host:port, separated by ,' },
  seed: { label: 'Seed this project', line: 'Share its files with other players while DeltaPatcher runs.' },
  seed_copy: { label: 'Seed from a copy', line: 'Seeding reads a copy, filled from the game folder; it takes space.' },
  // Window
  close_to_tray: { label: 'Close to the tray', line: 'Closing the window keeps DeltaPatcher in the tray.' },
  minimize_to_tray: { label: 'Minimize to the tray', line: 'Minimizing hides the window in the tray.' },
  start_with_system: { label: 'Start with the computer', line: 'DeltaPatcher starts straight into the tray.' },
  check_updates: { label: 'Look for a newer DeltaPatcher', line: 'Once a day; nothing is downloaded without asking.', more: true },
  // Projects
  parallel_updates: { label: 'Projects updated at once', line: 'How many projects Update all runs at the same time.', more: true },
  check_every_hours: { label: 'Hours between checks', line: 'Every project is checked at the start, then this often.' },
  auto_update: { label: 'Update by themselves', line: 'Projects with an update are updated when they are checked.' },
  torrents: { label: 'Torrents', line: 'Refuse: DeltaPatcher never uses BitTorrent, whatever a project asks.' },
  torrent_port: { label: 'Torrent port', line: 'The port seeding listens on; 0: any free port.', more: true },
  seed_upload_kbps: { label: 'Upload limit', line: 'The most torrents send to others, in KB/s; 0: no limit.', more: true },
  torrent_port_mapping: { label: 'Port mapping', line: 'Ask the router to forward the torrent port (UPnP, NAT-PMP).', more: true },
  seed_copies: { label: 'Seed copies', line: "Empty: inside each game's folder. A folder: all copies there." }
}

// a setting's words; one the window does not know yet keeps its own name
export function wordsOf(key: string): SettingWords {
  return WORDS[key] ?? { label: key, line: '' }
}

// a section's settings as the page shows them: the ones shown, then the ones under More, each in its own order
export function splitMore(keys: string[]): { shown: string[]; more: string[] } {
  return { shown: keys.filter((k) => !wordsOf(k).more), more: keys.filter((k) => wordsOf(k).more) }
}
