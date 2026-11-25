// Seeding as the window shows it: the seed run (seed --all --json, one long engine run beside everything else,
// as the local server is), each project's line from the run's "seeding" events, when the run starts and when it
// starts again, why seeding is not running, the words of the import dialog about torrents, and the rules of the
// torrent settings the window checks while they are typed. Nothing here uses the window, so a test can run it as
// it is.

// the copy of its release a project seeds from (kind copy): where it is, what it holds on disk (bytes), whether it
// is filling (or tried again), ready, stopped by a full drive or waiting (the row's why says why when it is not
// ready), and the bytes of the release's blobs it holds (done) of all (total)
export interface SeedCopy {
  path: string
  bytes: number
  state: 'copying' | 'ready' | 'full' | 'waiting'
  done: number
  total: number
}

// one project of the seed run, as the run says every 5 seconds (the engine's "seeding" event): its state, why when
// it is not seeded, its release, its peers, what it sends (bytes a second) and what it sent this run (bytes)
export interface SeedRow {
  project: string
  file: string
  folder: string
  kind: 'folder' | 'store' | 'copy'
  state: 'seeding' | 'paused' | 'waiting' | 'stopped'
  why: string
  release: string
  peers: number
  upload_rate: number
  sent: number
  copy?: SeedCopy
}

// a row with when it came (milliseconds)
export interface TimedRow extends SeedRow {
  at: number
}

// the settings that say which projects seed: a run reads them again by itself, and with no run going a change to
// one may start one
export const SEED_KEYS = ['seed', 'torrent', 'torrent_id', 'torrents']
// the settings a run reads only as it starts: a change to one starts it again
export const RESTART_KEYS = ['torrent_port', 'seed_upload_kbps', 'torrent_port_mapping']
// where a seeded project's files are, and whom it trusts: a run reads them only as it starts, so a change to one in
// a project set to seed starts it again
export const PLACE_KEYS = ['install_dir', 'store_dir', 'source_url', 'public_url', 'trust_key']
// how long after a run ended by itself it starts again (milliseconds): a few times at most
export const RETRY = [10_000, 60_000, 300_000]
// a run that went on this long before it ended counts its ends from none again
export const STEADY = 10 * 60_000
// a row older than this is of a project the run no longer seeds: rows come every 5 seconds
export const STALE = 15_000
// the code of a run that Force stop ended (shared/forceStop.ts)
const FORCE_STOPPED = 'force-stopped'
// the codes of a run that cannot seed until a seed setting changes
const WAIT: Record<string, string> = {
  'torrents-refused': 'Not seeding: torrents are refused in the settings (Settings, Torrents).',
  'no-torrent-support': 'Not seeding: this DeltaPatcher has no BitTorrent support.',
  'nothing-to-seed': 'Not seeding: no project is set to seed.'
}
export const REFUSED_WHY = WAIT['torrents-refused']

interface Seedable {
  file: string
  missing?: boolean
  torrent?: boolean
  seed?: boolean
}

// torrents = allow lets BitTorrent in; refuse, or anything else, refuses it (as the engine reads it). Not known
// yet counts as the default, allow: the engine says so when they are refused.
export function torrentsRefused(torrents: unknown): boolean {
  return torrents !== undefined && torrents !== null && String(torrents).trim().toLowerCase() !== 'allow'
}

// the projects that ask to be seeded: shared by torrent, seed = yes, their settings file there
export function seedable<P extends Seedable>(projects: P[]): P[] {
  return projects.filter((p) => !p.missing && p.torrent && p.seed)
}

// the seed run is wanted: a project asks to be seeded, torrents are allowed and Force stop is not on
export function wantsSeeding(projects: Seedable[], torrents: unknown, stopped: boolean): boolean {
  return !stopped && !torrentsRefused(torrents) && seedable(projects).length > 0
}

// Force stop: seed = no for every project that has seed = yes (its settings file there), each with its own
// --project, so the window's withProject() adds none; config set runs while the stop holds
export function seedOffArgs(projects: Seedable[]): string[][] {
  return projects.filter((p) => !p.missing && p.seed).map((p) => ['config', 'set', 'seed=no', `--project=${p.file}`])
}

// what comes after a seed run's end
export type SeedNext =
  | { next: 'nothing' }
  | { next: 'start' }
  | { next: 'wait'; why: string }
  | { next: 'retry'; after: number; why: string; retries: number }

export interface SeedEnd {
  ok: boolean
  code?: string
  message?: string
}

export interface SeedContext {
  // the window stopped the run to start it again (a setting it reads only as it starts changed)
  restarting: boolean
  stopped: boolean
  // a run is wanted now (wantsSeeding, with the settings read again)
  wanted: boolean
  refused: boolean
  // how long the run went on (milliseconds), and how many runs in a row ended by themselves before it
  ranFor: number
  retries: number
}

function seconds(ms: number): string {
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s} seconds` : s === 60 ? 'a minute' : `${Math.round(s / 60)} minutes`
}

// an engine's message inside a sentence of the window: without the full stop it may end with
function clause(text: string | undefined, otherwise: string): string {
  return (text ?? '').trim().replace(/[.\s]+$/, '') || otherwise
}

// A run ended. Force stop: nothing. Stopped by the window to start again: it starts. Refused, no BitTorrent here,
// or nothing to seed: not again until a seed setting changes, and why is said. Nothing asks for a run any more:
// said, and no run. Otherwise it ended by itself: it starts again after RETRY[n], a few times at most.
export function afterRun(end: SeedEnd, c: SeedContext): SeedNext {
  if (c.stopped || end.code === FORCE_STOPPED) return { next: 'nothing' }
  if (c.restarting) return c.wanted ? { next: 'start' } : { next: 'nothing' }
  if (!end.ok && end.code && WAIT[end.code]) return { next: 'wait', why: WAIT[end.code] }
  if (!end.ok && end.code === 'refused') return { next: 'wait', why: `Not seeding: ${clause(end.message, 'the window may not run it')}.` }
  if (!c.wanted) {
    if (c.refused) return { next: 'wait', why: 'Seeding stopped: torrents are refused in the settings (Settings, Torrents).' }
    return { next: 'wait', why: end.ok ? 'Seeding stopped: no project is set to seed now.' : `Seeding stopped: ${clause(end.message, 'it failed')}.` }
  }
  const retries = c.ranFor >= STEADY ? 0 : c.retries
  const why = end.ok ? 'it ended' : clause(end.message, 'it failed')
  if (retries >= RETRY.length) {
    return { next: 'wait', why: `Seeding stopped by itself (${why}): it starts again when a seed setting changes, or when DeltaPatcher starts.` }
  }
  const after = RETRY[retries]
  return { next: 'retry', after, retries: retries + 1, why: `Seeding stopped by itself (${why}): it starts again in ${seconds(after)}.` }
}

// the rows with this one in (by its settings file), and without the ones the run no longer sends
export function withRow(rows: Record<string, TimedRow>, row: SeedRow, now: number): Record<string, TimedRow> {
  const next: Record<string, TimedRow> = {}
  for (const [file, r] of Object.entries(rows)) if (now - r.at < STALE) next[file] = r
  next[row.file] = { ...row, at: now }
  return next
}

// how many projects are being seeded now (the tray says so)
export function seedingCount(rows: Record<string, SeedRow>): number {
  return Object.values(rows).filter((r) => r.state === 'seeding').length
}

// bytes a second, as the row says them: 120 KB/s, 1.5 MB/s
export function rate(bytesPerSecond: number): string {
  const kb = Math.max(0, Number(bytesPerSecond) || 0) / 1024
  return kb < 1000 ? `${Math.round(kb)} KB/s` : `${(kb / 1024).toFixed(1)} MB/s`
}

// bytes, as the window says them elsewhere: 512 bytes, 4.2 MB
export function size(bytes: number): string {
  let v = Math.max(0, Number(bytes) || 0)
  if (v < 1024) return `${v} bytes`
  const units = ['KB', 'MB', 'GB', 'TB']
  let u = -1
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024
    u++
  }
  return `${v.toFixed(1)} ${units[u]}`
}

// how far a copy's filling is: "copying for seeding: 4.2 MB of 20.0 MB"
export function copyingWords(c: SeedCopy): string {
  return `copying for seeding: ${size(c.done)} of ${size(c.total)}`
}

// a project's line on the Projects page, from the seed run's row: "Seeding: 3 peers, 120 KB/s up" (", from a copy"
// when it seeds from a copy of its release, how far that copy is while it fills, and why while it is not ready),
// paused while an update changes its folder, or why it is not seeded
export function seedLine(row: SeedRow): string {
  const c = row.copy
  const copying = c && c.state === 'copying'
  if (row.state === 'seeding') {
    const peers = Math.max(0, Number(row.peers) || 0)
    const line = `Seeding: ${peers} ${peers === 1 ? 'peer' : 'peers'}, ${rate(row.upload_rate)} up`
    if (!c) return line
    const why = c.state !== 'ready' && row.why ? `; ${clause(row.why, '')}.` : ''
    return copying ? `${line}, from a copy; ${copyingWords(c)}${why}` : `${line}, from a copy${why}`
  }
  if (row.state === 'paused') return 'Seeding paused while an update changes its folder.'
  if (row.state === 'waiting' && !row.why && c && copying) return `Copying for seeding: ${size(c.done)} of ${size(c.total)}.`
  if (row.state === 'waiting' && !row.why) return 'Getting ready to seed.'
  return row.why ? `Not seeded: ${row.why}.` : 'Not seeded.'
}

// how much the seed copies take on disk now, from the rows the window has of the seed run; null when no row says
// it seeds from a copy
export function copiesBytes(rows: Record<string, SeedRow>): number | null {
  const copies = Object.values(rows).filter((r) => r.copy)
  return copies.length ? copies.reduce((n, r) => n + Math.max(0, Number(r.copy?.bytes) || 0), 0) : null
}

// the seed copies folder as the engine reads seed_copies: a relative path starts at the main settings file's folder
export function copiesFolder(value: string, mainPath: string): string {
  const v = value.trim()
  if (!v || /^([A-Za-z]:[\\/]|[\\/]|~)/.test(v)) return v
  const at = Math.max(mainPath.lastIndexOf('/'), mainPath.lastIndexOf('\\'))
  return at > 0 ? `${mainPath.slice(0, at)}${mainPath.includes('\\') ? '\\' : '/'}${v}` : v
}

// A project's line on the Projects page: for a project set to seed, its row of the seed run, else why it is not
// seeded (torrents refused, the run's last word) or that the run is starting; '' for any other project (a row of a
// project set to seed = no a moment ago is its last one before the run drops it).
export function projectSeedLine(
  p: Seedable,
  row: SeedRow | undefined,
  s: { refused: boolean; running: boolean; why: string; stopped: boolean }
): string {
  if (!p.torrent || !p.seed || p.missing || s.stopped) return ''
  if (row) return seedLine(row)
  if (s.refused) return REFUSED_WHY
  if (s.running) return 'Getting ready to seed.'
  return s.why
}

// ---- the import dialog

// the mark of a project shared by torrent, and its hover text
export function torrentMark(only: boolean): { text: string; hint: string } {
  return only
    ? { text: 'By torrent only', hint: 'It has no address: players download it from each other only.' }
    : { text: 'By torrent', hint: 'Shared by torrent too: players also download it from each other.' }
}

// what seeding a project means, under its switch in the import dialog
export const SEED_IT = 'Other players download from you while DeltaPatcher runs.\n' +
  'It keeps a copy of the release for that in its folder, or the folder Settings > Seed copies names, as large as the ' +
  'release (less on drives that share space).\n' +
  'Only files DeltaPatcher has checked are offered; others see your IP address.'

// a project shared by torrent while torrents are refused here: what that leaves (the engine's words)
export function refusedLine(hasAddress: boolean): string {
  return `Torrents are refused in your settings (Settings, Torrents): ${hasAddress ? 'it updates from its address only' : 'it updates only by torrent, so it cannot update here'}.`
}

// ---- the torrent settings, checked as they are typed ('' when right): the engine's own rules

const LABEL = /^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$/
const DEVICES = new Set(['con', 'prn', 'aux', 'nul', ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((i) => [`com${i}`, `lpt${i}`])])
const RESERVED = ['blobs', 'sigs', 'manifest.json', 'manifest.previous.json', 'release.torrent', 'release.item']
export const MAX_SEEDS = 20

function hostProblem(host: string): boolean {
  if (host.startsWith('[') && host.endsWith(']')) {
    const inner = host.slice(1, -1)
    return !(inner && /^[0-9A-Fa-f:.]+$/.test(inner) && inner.includes(':'))
  }
  if (!host || host.length > 253) return true
  return !host.split('.').every((part) => LABEL.test(part))
}

// one seed: host:port, the host a name, an IPv4 address or [an IPv6 address], the port 1 to 65535
export function seedProblem(text: string): string {
  if (!text.includes(':') || text.length > 300) return `${text}: give it as host:port.`
  const at = text.lastIndexOf(':')
  const host = text.slice(0, at)
  const port = text.slice(at + 1)
  if (!/^[1-9]\d{0,4}$/.test(port) || Number(port) > 65535) return `${text}: the port after the : is 1 to 65535.`
  if (hostProblem(host)) return `${text}: a host name or address before the :.`
  return ''
}

// the seeds setting: host:port, separated by , ; or spaces, at most MAX_SEEDS
export function seedsProblem(text: string): string {
  const items = text.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean)
  for (const item of items) {
    const problem = seedProblem(item)
    if (problem) return problem
  }
  return new Set(items).size > MAX_SEEDS ? `At most ${MAX_SEEDS} seeds.` : ''
}

// a torrent's name: empty (the default), or letters, digits and . _ - (not at its start, no dot at its end), at
// most 60 characters, not a name Windows keeps for a device nor the name of a file or folder of the store
export function torrentNameProblem(text: string): string {
  const name = text.trim()
  if (!name) return ''
  if (name.length > 60) return 'At most 60 characters.'
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name) || name.endsWith('.')) {
    return 'Letters, digits and . _ - only, starting with a letter or a digit, with no dot at its end.'
  }
  if (DEVICES.has(name.split('.')[0].toLowerCase())) return `${name} is a name Windows keeps for a device: choose another.`
  if (RESERVED.includes(name.toLowerCase())) return `${name} is the name of a file or folder of the store: choose another.`
  return ''
}

// a project has its torrent id (made when its publisher first shared it by torrent; a player's comes with its
// project file): torrents can be turned on again without signing
export function hasTorrentId(id: unknown): boolean {
  return /^[0-9a-f]{32}$/.test(String(id ?? '').trim().toLowerCase())
}

export const NEEDS_SIGNING = 'Needs signed releases: turn on Sign each release first.'

// The switches of torrents that cannot work yet, and why ('' when they can): sharing a project by torrent needs
// signed releases, unless it has its torrent id already (or is shared now, to be turned off); only a project shared
// by torrent can seed (or is set to, to be turned off).
export function torrentSwitches(settings: Record<string, unknown>): { torrent: string; seed: string } {
  return {
    torrent: !settings.sign && !settings.torrent && !hasTorrentId(settings.torrent_id) ? NEEDS_SIGNING : '',
    seed: !settings.torrent && !settings.seed ? 'Nothing to seed: this project is not shared by torrent.' : ''
  }
}

// the torrent port: 1 to 65535, or 0 for any free port
export function portProblem(text: string): string {
  return /^\d{1,5}$/.test(text.trim()) && Number(text) <= 65535 ? '' : 'A port from 1 to 65535, or 0 for any free port.'
}
