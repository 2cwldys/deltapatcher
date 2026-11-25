// What the window says and does for the two types of project file: public (for players, as it always was) and
// private (for the publisher, to keep changing and releasing a project from any computer of theirs). A private
// file holds what releasing needs, never a key or a password; opening one sets up releasing here, with this
// computer's own folders and keys. Nothing here uses the window, so a test can run it as it is.

export type ExportType = 'public' | 'private'

// what releasing a project of a private file needs: its settings of the allow-list, as the engine reads them
export interface PrivatePart {
  name: string
  version: string
  protect: string
  leave_out: string
  sign: boolean
  htaccess: boolean
  share_updates: boolean
  show_files: boolean
  signing_public_key: string
  deploy_mode: string
  ssh_host: string
  ssh_user: string
  ssh_port: number
  remote_root: string
  public_url: string
  access_log?: string
  torrent: boolean
  torrent_id: string
  torrent_name: string
  seeds: string
  backup_keep_daily: number
  backup_keep_weekly: number
  backup_keep_monthly: number
  backup_max_size: string
}

// the server's host keys a private file gives, and what this computer has of them: new (it trusts no key for that
// server yet), known (it trusts every one of them), other (it trusts keys for that server, and not all of these:
// none is added; here lists the ones it trusts), '' (none in the file), unreadable
export interface HostKeys {
  host: string
  keys: { type: string; fingerprint: string }[]
  here?: { type: string; fingerprint: string }[]
  state: '' | 'new' | 'known' | 'other' | 'unreadable'
  why?: string
}

// one setting a newer private file changes in the project it updates
export interface PrivateChange {
  key: string
  label: string
  from: string
  to: string
}

// the type comes first in the export dialog: its label and what it says, one short sentence a line
export const EXPORT_TYPES: { value: ExportType; label: string; hint: string }[] = [
  { value: 'public', label: 'Public: for players', hint: 'What players need to keep it up to date.\nThey add it with a double-click.' },
  { value: 'private', label: 'Private: for you, on any computer', hint: 'What releasing it needs.\nOpen it on your other computer.' }
]

// what the export dialog says of a private file
export const PRIVATE_EXPORT =
  "It holds your server's address and login name, never a key or a password.\nKeep it to yourself.\n" +
  'Bring your signing key separately: a USB stick, not chat or email.'

// what the import dialog says of a private file, and of the signing key that moves by hand
export const PRIVATE_IMPORT =
  'A private project file: it sets up releasing from this computer.\nIt holds no key and no password.'
export const KEY_BY_HAND = 'Bring your signing key from your other computer by hand: a USB stick, not chat or email.'

// the file's name a save dialog offers: "<project> (private)" for one project
export function exportName(titles: string[], type: ExportType): string {
  const name = titles.length === 1 ? titles[0] : 'Projects'
  return type === 'private' ? `${name} (private)` : name
}

// why a project cannot go into a file of this type ('' when it can): an address players update from for a public
// file (as before); for a private one, an address or a torrent, and signed releases (the file is signed with
// their signing key)
export function exportProblem(p: { address: string; torrent?: boolean; sign?: boolean }, type: ExportType): string {
  if (type === 'public') return p.address ? '' : 'No http(s) address players update from: set public_url on its Release page.'
  if (!p.address && !p.torrent) return 'No address players update from, and not shared by torrent: set public_url on its Release page.'
  return p.sign ? '' : 'Its releases are not signed: turn on Sign each release on its Release page.'
}

type Exportable = { file: string; address: string; torrent?: boolean; sign?: boolean }

// the ticks of the export dialog once its type is chosen: a project that cannot go into a file of that type is
// unticked (the line under it says why), so a tick the person cannot take off never holds Export back
export function picksFor(type: ExportType, projects: Exportable[], picks: Record<string, boolean>): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  for (const p of projects) out[p.file] = Boolean(picks[p.file]) && !exportProblem(p, type)
  return out
}

// the projects that go into the file: ticked, and able to go into a file of that type
export function exportChosen<T extends Exportable>(projects: T[], picks: Record<string, boolean>, type: ExportType): T[] {
  return projects.filter((p) => picks[p.file] && !exportProblem(p, type))
}

// the engine run of a private export
export function privateExportArgs(files: string[], out: string, passphrase: boolean): string[] {
  return ['project', 'export', ...files, `--out=${out}`, '--private', ...(passphrase ? ['--passphrase-stdin'] : [])]
}

// a private file's project as the import dialog keeps it: the file's part, and what the person gives here. older:
// the file was made before the one applied to the project it updates (or is that one again), made and applied_made
// the two times; it is applied only when the person says so (applyOlder, Apply it anyway)
export interface PrivateRow {
  title: string
  name: string
  pick: boolean
  asNew: boolean
  updates?: { project: string; file: string }
  older?: boolean
  made?: string
  applied_made?: string
  applyOlder?: boolean
  private?: PrivatePart
  torrent?: unknown
  host_keys?: HostKeys
  publishSrc: string
  signingKey: string
  sshKey: string
  deployFolder: string
  seed: boolean
  trustHostKeys: boolean
}

// it updates the project here it is (not added as a new one)
function updatesHere(e: PrivateRow): boolean {
  return Boolean(e.updates) && !e.asNew
}

// the projects ticked that the engine runs for: all but one the file would update while it is older than the one
// applied there, and the person did not say Apply it anyway
export function privateRuns<T extends PrivateRow>(rows: T[]): T[] {
  return rows.filter((e) => e.pick && !(updatesHere(e) && e.older && !e.applyOlder))
}

// the engine run that sets up (or updates) one project of a private file: a project added gets this computer's
// folders and keys; one updated in place keeps its own (an older file only with the person's Apply it anyway); the
// host keys only when the person said they are theirs, and only the ones shown (by their fingerprints: a file
// changed since adds nothing)
export function privateImportArgs(file: string, e: PrivateRow, torrentsRefused: boolean): string[] {
  const args = ['project', 'import', file, `--only=${e.title}`, '--yes']
  if (!updatesHere(e)) {
    if (e.updates) args.push('--as-new')
    if (e.name.trim() !== e.title) args.push(`--name=${e.name.trim()}`)
    const mode = e.private?.deploy_mode
    const given: [string, string][] = [['--publish-src', e.publishSrc], ['--signing-key', e.signingKey]]
    if (mode === 'sftp') given.push(['--ssh-key', e.sshKey])
    if (mode === 'folder') given.push(['--deploy-folder', e.deployFolder])
    for (const [option, value] of given) if (value.trim()) args.push(`${option}=${value.trim()}`)
    if (e.seed && e.torrent && !torrentsRefused) args.push('--seed')
  } else if (e.older && e.applyOlder) {
    args.push('--apply-older')
  }
  const shown = (e.host_keys?.keys ?? []).map((k) => k.fingerprint)
  if (e.trustHostKeys && e.host_keys?.state === 'new' && shown.length) args.push(`--host-key-fingerprints=${shown.join(',')}`)
  return args
}

// the import dialog's button for a private file
export function privateImportButton(rows: PrivateRow[], working = false): string {
  if (working) return 'Setting up...'
  const runs = privateRuns(rows)
  const upd = runs.filter(updatesHere).length
  const add = runs.length - upd
  if (upd && add) return `Set up ${add}, update ${upd}`
  if (upd) return upd === 1 ? 'Update it' : `Update ${upd} projects`
  return 'Set up releasing'
}

// the line under the server's host keys
export function hostKeysLine(h: HostKeys | undefined): string {
  if (!h || !h.state) return ''
  if (h.state === 'known') return 'This computer trusts them already.'
  if (h.state === 'other') return `The host keys this computer trusts for ${h.host} differ from the file's.\nThe file's are not added.`
  if (h.state === 'unreadable') return `They cannot be checked here: ${h.why ?? 'OpenSSH is missing'}.\nThe first release asks about the server.`
  return 'Trust them only if this file is yours.\nThen the first release does not ask about the server.'
}

// what the window says once a private file is set up: one line for each project that needs something more
export function setUpNotes(added: { project: string; key_check?: string; host_keys?: string; publish_src?: string }[]): string[] {
  const lines: string[] = []
  for (const a of added) {
    if (!a.publish_src) lines.push(`${a.project}: choose the folder to release on its Release page.`)
    if (a.key_check === 'first release') lines.push(`${a.project}: the first release checks your signing key, when it asks its passphrase.`)
    if (a.host_keys === 'not added' || a.host_keys === 'unreadable') lines.push(`${a.project}: the first release asks about the server.`)
    if (a.host_keys === 'other') lines.push(`${a.project}: this computer trusts other host keys for its server, so the file's were not added.`)
  }
  return lines
}

// a seed run is started (or told) when the engine set up a project to seed: its own answer, project by project
export function seedAfterSetUp(added: { seed?: boolean }[]): boolean {
  return added.some((a) => a.seed === true)
}

// the line of a project of a private file that this computer only plays: the file never changes it
export function playerLine(name: string): string {
  return `You have ${name} as a player.\nThis file sets up releasing it, as a project of its own.`
}

// when a project file was made, as a person reads it: 2026-11-25 14:02 UTC
export function madeWords(made: string): string {
  const m = /^(\d{4}-\d\d-\d\d)T(\d\d:\d\d):\d\dZ$/.exec(made)
  return m ? `${m[1]} ${m[2]} UTC` : 'at a time not known'
}

// a file older than the one applied to the project it updates (or that one again): both times, as the clocks of two
// computers give them, so a wrong one shows
export function olderLine(made: string, applied: string): string {
  return `This file was made ${madeWords(made)}.\nThe one applied here was made ${madeWords(applied)}.\n` +
    'Opening it puts back what it says.'
}

// the secondary button beside that line, and its way back
export const APPLY_OLDER = 'Apply it anyway'
export function leaveIt(name: string): string {
  return `Leave ${name} as it is`
}

// what the import dialog says of a project here the file updates: what changes; for an older file, both times and
// whether it is applied anyway
export function inPlaceLine(name: string, e: { older?: boolean; made?: string; applied_made?: string; applyOlder?: boolean; changes?: PrivateChange[] }): string {
  const changes = e.changes ?? []
  if (e.older) {
    const what = e.applyOlder
      ? `This updates ${name} anyway${changes.length ? ':' : '.'}`
      : `${name} stays as it is, unless you apply it anyway.`
    return `${olderLine(e.made ?? '', e.applied_made ?? '')}\n${what}`
  }
  return changes.length ? `This updates ${name}:` : `This updates ${name}.\nNothing in it changes.`
}

// the line of a project here of the file's name that releases here, with a signing key DeltaPatcher does not know
// yet: the file does not update it
export function keyUnknownLine(name: string): string {
  return `${name} releases here too, but DeltaPatcher does not know its signing key yet.\n` +
    'Release it once or export a private file from it, then open this file again.\nOr set this one up as a separate project.'
}
