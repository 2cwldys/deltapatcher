// The Statistics card's view of the engine's statistics document (format deltapatcher-stats, version 1): the
// numbers it shows, and each day's bar as a fraction of the busiest day. Nothing here uses the window, so a
// test can run it as it is.

export interface ReleaseInfo {
  name: string
  version: string
  published: string
  files: number
  bytes: number
  blobs: number
}

export interface ReleasesSection {
  store: string
  current: ReleaseInfo
  change: { from: { name: string; version: string; published: string }; new: number; changed: number; removed: number; download_bytes: number } | null
  store_files: number
  store_bytes: number
  history_total: number
}

export interface DayCount {
  date: string
  checks: number
  downloads: number
  bytes: number
  players: number
}

export interface DownloadsSection {
  days: DayCount[]
  total: { checks: number; downloads: number; bytes: number; players: number }
  releases: { release: string | null; players: number }[]
  releases_more: number
  lines: number
  unreadable: number
  log: string
  read_from: string
}

export interface Bar {
  date: string
  value: number
  // 0 to 1: the share of the busiest day
  height: number
}

export interface BarRow {
  key: 'checks' | 'downloads' | 'players'
  label: string
  bars: Bar[]
  max: number
}

export interface StatsView {
  // a document this card reads (its format and version)
  ok: boolean
  days: number
  release?: ReleaseInfo
  change?: { from: string; new: number; changed: number; removed: number; download: number }
  store?: { bytes: number; files: number; releases: number }
  releasesProblem: string
  downloads?: {
    total: DownloadsSection['total']
    rows: BarRow[]
    first: string
    last: string
    releases: { label: string; players: number; share: number }[]
    lines: number
    unreadable: number
    log: string
    readFrom: string
  }
  downloadsProblem: string
}

const ROWS: { key: BarRow['key']; label: string }[] = [
  { key: 'checks', label: 'Update checks' },
  { key: 'downloads', label: 'Downloads' },
  { key: 'players', label: 'Players' }
]

// each day's count of `key`, as a bar: its height a share of the day with the most
export function bars(days: DayCount[], key: BarRow['key']): { bars: Bar[]; max: number } {
  const max = days.reduce((m, d) => Math.max(m, Number(d[key]) || 0), 0)
  return { max, bars: days.map((d) => ({ date: d.date, value: Number(d[key]) || 0, height: max > 0 ? (Number(d[key]) || 0) / max : 0 })) }
}

// the releases players are on, each with its share of them; an updater that did not say is "unknown"
export function shares(releases: DownloadsSection['releases'], more: number): { label: string; players: number; share: number }[] {
  const all = releases.map((r) => ({ label: r.release ?? 'unknown', players: r.players }))
  if (more > 0) all.push({ label: 'others', players: more })
  const total = all.reduce((n, r) => n + r.players, 0)
  return all.map((r) => ({ ...r, share: total > 0 ? r.players / total : 0 }))
}

export function when(text: string): string {
  return String(text || '').replace('T', ' ').replace('Z', ' UTC')
}

export function readStats(doc: unknown): StatsView {
  const d = (doc ?? {}) as { format?: string; version?: number; days?: number; sections?: Record<string, unknown>; problems?: Record<string, string> }
  const problems = d.problems ?? {}
  const view: StatsView = { ok: false, days: Number(d.days) || 0, releasesProblem: '', downloadsProblem: '' }
  if (d.format !== 'deltapatcher-stats' || d.version !== 1 || typeof d.sections !== 'object' || d.sections === null) return view
  view.ok = true
  const rel = d.sections.releases as ReleasesSection | undefined
  if (rel) {
    view.release = { ...rel.current, version: rel.current.version || '(no version)' }
    if (rel.change) {
      const c = rel.change
      view.change = { from: c.from.version || 'the release before', new: c.new, changed: c.changed, removed: c.removed, download: c.download_bytes }
    }
    view.store = { bytes: rel.store_bytes, files: rel.store_files, releases: rel.history_total }
  } else {
    view.releasesProblem = String(problems.releases ?? '')
  }
  const dl = d.sections.downloads as DownloadsSection | undefined
  if (dl) {
    view.downloads = {
      total: dl.total,
      rows: ROWS.map((r) => ({ ...r, ...bars(dl.days, r.key) })),
      first: dl.days[0]?.date ?? '',
      last: dl.days[dl.days.length - 1]?.date ?? '',
      releases: shares(dl.releases, dl.releases_more),
      lines: dl.lines,
      unreadable: dl.unreadable,
      log: dl.log,
      readFrom: dl.read_from
    }
  } else {
    view.downloadsProblem = String(problems.downloads ?? '')
  }
  return view
}
