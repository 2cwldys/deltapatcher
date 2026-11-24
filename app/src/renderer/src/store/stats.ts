// The Statistics card's view of the engine's statistics document (format deltapatcher-stats, version 1): the
// numbers it shows, each day's bar as a fraction of the busiest day, and the card's counting (statsCounter).
// Nothing here uses the window, so a test can run it as it is.

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
  // past a number of players the engine estimates them: its player counts are then "about"
  players_estimated?: boolean
  lines: number
  unreadable: number
  log: string
  read_from: string
}

// one row of the releases players are on: `key` is its own, never a release's name
export interface Share {
  key: string
  label: string
  players: number
  share: number
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
    releases: Share[]
    // the player counts are estimates
    about: boolean
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

// the releases players are on, each with its share of them; an updater that did not say is "unknown", the
// releases past the listed ones are "others". A release may be named "unknown" or "others" too: each row's
// key is its own, a release's starting with "release:".
export function shares(releases: DownloadsSection['releases'], more: number): Share[] {
  const all = releases.map((r) =>
    r.release === null ? { key: 'unknown', label: 'unknown', players: r.players } : { key: `release:${r.release}`, label: r.release, players: r.players })
  if (more > 0) all.push({ key: 'others', label: 'others', players: more })
  const total = all.reduce((n, r) => n + r.players, 0)
  return all.map((r) => ({ ...r, share: total > 0 ? r.players / total : 0 }))
}

// a count of players as the card says it: "about" when the engine estimated it
export function players(n: number, about: boolean): string {
  return `${about ? 'about ' : ''}${n}`
}

// The line a folded Statistics card shows: the update checks and players of the period, or what stands in the
// way of counting them ('' while there is nothing to say yet).
export function statsSummary(card: StatsState, accessLog: string): string {
  const { view, error, loading } = card
  if (error) return 'The statistics could not be counted: open it to see why.'
  if (!view) return loading ? 'Counting...' : ''
  const dl = view.downloads
  if (dl) {
    const checks = dl.total.checks
    const n = dl.total.players
    return `${checks} update ${checks === 1 ? 'check' : 'checks'} and ${players(n, dl.about)} ${n === 1 ? 'player' : 'players'} in the last ${view.days || 30} days`
  }
  const release = view.release ? `Release ${view.release.version}; ` : ''
  if (!accessLog) return `${release}players are counted once the access log is named`
  return `${release}players could not be counted: open it to see why`
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
      about: Boolean(dl.players_estimated),
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

export interface StatsAnswer {
  ok: boolean
  data?: unknown
  message?: string
}

// what the card shows: the statistics, why they could not be counted, and whether a count is going
export interface StatsState {
  view: StatsView | null
  error: string
  loading: boolean
}

// The card's counting. Each project's last statistics are kept while another is shown. A count goes to the
// project that was on screen when it was asked for: its answer is kept for that project (unless a newer count's
// is kept already), and shown only while that project is still on screen and no newer count was asked for
// since (a slow count never shows one project's numbers on another's card).
export function statsCounter(
  run: (file: string) => Promise<StatsAnswer>,
  onScreen: () => string,
  show: (card: StatsState) => void
): { kept: Map<string, StatsView>; count: () => Promise<void>; shown: (file: string) => StatsState } {
  const kept = new Map<string, StatsView>()
  const keptFrom = new Map<string, number>()
  let asked = 0
  const shown = (file: string): StatsState => ({ view: kept.get(file) ?? null, error: '', loading: false })
  const count = async (): Promise<void> => {
    const n = ++asked
    const file = onScreen()
    show({ ...shown(file), loading: true })
    const r = await run(file)
    if (r.ok && n > (keptFrom.get(file) ?? 0)) {
      kept.set(file, readStats(r.data))
      keptFrom.set(file, n)
    }
    if (n !== asked || onScreen() !== file) return
    show(r.ok ? shown(file) : { view: kept.get(file) ?? null, error: String(r.message || 'The engine gave no reason.'), loading: false })
  }
  return { kept, count, shown }
}
