// Force stop, the panic button: every engine run the app started is ended, and no other starts until the stop
// is lifted. Only what draws the window and keeps what was typed still runs, all of it on this computer: the
// version (not its BitTorrent self-test, which makes a session), the list of projects, the list of backups, and
// reading or writing settings (Force stop itself turns seeding off with them).

// the code of a run that Force stop ended, or refused to start
export const FORCE_STOPPED = 'force-stopped'

export function allowedWhileStopped(args: readonly string[]): boolean {
  const [command, sub] = args
  if (command === 'version') return !args.includes('--torrent')
  if (command === 'projects') return true
  if (command === 'backup') return sub === 'list'
  return command === 'config' && (sub === 'get' || sub === 'set')
}
