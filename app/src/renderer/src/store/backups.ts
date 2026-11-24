// The Backups card and the app's automatic backups: the engine's backup list as the card shows it, the
// question a restore asks, when a project is due a backup, what an automatic backup's answer means, and the
// size limit as typed. Nothing here uses the window, so a test can run it as it is.

export interface ReleaseShort {
  name: string
  version: string
  files: number
  bytes: number
}

export interface BackupInfo {
  name: string
  made: string
  from: string
  release: ReleaseShort
}

// the system scheduler's last run of a project's backups (backup schedule), as the engine noted it
export interface ScheduledRun {
  time: string
  schedule: string
  ok: boolean
  made: boolean
  name: string
  code: string
  message: string
}

export interface BackupList {
  folder: string
  // the folder the projects' backup folders are in (backup_dir, else the default)
  base?: string
  backups: BackupInfo[]
  // dated folders that cannot be read: nothing is removed while there is one
  unreadable?: { name: string; why: string }[]
  bytes: number
  scheduled?: ScheduledRun | null
}

export interface BackupRow {
  name: string
  made: string
  release: string
  files: number
  bytes: number
}

const HOUR = 60 * 60 * 1000
// after an automatic backup that failed, or found another backup of the project running: the next try
export const RETRY = 15 * 60 * 1000

export function releaseWords(r: Partial<ReleaseShort> | null | undefined): string {
  return r ? `${r.name || '(unnamed)'} ${r.version ?? ''}`.trim() : 'nothing'
}

export function when(text: string | undefined): string {
  return String(text || '').replace('T', ' ').replace('Z', ' UTC')
}

// the backups, the newest first, as rows
export function backupRows(list: BackupList | null | undefined): BackupRow[] {
  return (list?.backups ?? []).map((b) => ({
    name: b.name,
    made: when(b.made),
    release: releaseWords(b.release),
    files: b.release?.files ?? 0,
    bytes: b.release?.bytes ?? 0
  }))
}

// The line the folded list shows: how many backups, the newest one's day (UTC), what they hold together
// (`size`, as the card writes it), and what makes them: the app every backup_every_hours hours, else the
// system's scheduler when it has run, else nothing but a click. `today` is today's date in UTC.
export function backupSummary(list: BackupList, hours: unknown, today: string, size: string): string {
  const h = Number(hours)
  const by = Number.isFinite(h) && h > 0 ? (h === 1 ? 'every hour' : `every ${h} hours`)
    : list.scheduled ? `${list.scheduled.schedule} by the system` : 'only when asked'
  const n = list.backups.length
  if (!n) return `No backups yet; one is made ${by}`
  const newest = list.backups[0].name
  const t = Date.parse(`${today}T00:00:00Z`)
  const yesterday = Number.isFinite(t) ? new Date(t - 24 * HOUR).toISOString().slice(0, 10) : ''
  const day = newest === today ? 'today' : newest === yesterday ? 'yesterday' : newest
  return `${n === 1 ? '1 backup' : `${n} backups`}, the newest ${day}, ${size}; made ${by}`
}

// the system scheduler's last run, in a line ('' when there was none)
export function scheduledLine(s: ScheduledRun | null | undefined): string {
  if (!s) return ''
  return `The system's last scheduled run, ${when(s.time)}: ${s.message || (s.ok ? 'it worked' : 'it failed')}`
}

// what a restore asks first, from what the engine says it replaces
export function restoreQuestion(d: { backup?: BackupInfo; live?: ReleaseShort | null; live_problem?: string; destination?: string }): { title: string; body: string } {
  const name = d.backup?.name ?? ''
  const players = d.live_problem
    ? `From then on, players get ${releaseWords(d.backup?.release)} there.\nThe release there now could not be read (${d.live_problem}).\n`
    : `From then on, players get ${releaseWords(d.backup?.release)} there instead of ${releaseWords(d.live)}.\n`
  return {
    title: `Restore the backup of ${name}?`,
    body:
      `${d.destination ?? ''}\n\n` +
      players +
      'Its blobs go first and its manifest last, as a release goes.\n' +
      'Nothing there is removed, except a file of the same name and the wrong size, which is replaced.'
  }
}

// whether a project is due an automatic backup: backup_every_hours (0 or less: never) since the last one
export function backupDue(now: number, last: number | null, hours: unknown): boolean {
  const h = Number(hours)
  if (!Number.isFinite(h) || h <= 0) return false
  return last === null || now - last >= h * HOUR
}

// When the app next backs a project up by itself (milliseconds; null: never): backup_every_hours after the
// newer of its newest backup and the app's last try. A try that failed, or found another backup of the
// project running, is tried again RETRY later, never later than backup_every_hours would.
export function nextBackup(hours: unknown, made: number | null, tried: number | null, retry: boolean): number | null {
  const h = Number(hours)
  if (!Number.isFinite(h) || h <= 0) return null
  if (retry && tried !== null && (made === null || made < tried)) return tried + Math.min(RETRY, h * HOUR)
  const times = [made, tried].filter((t): t is number => t !== null)
  return times.length ? Math.max(...times) + h * HOUR : 0
}

export interface AutoOutcome {
  // the line for the console, after the project's name
  text: string
  style: 'ok' | 'warn' | 'err'
  // a failure: said in the status bar and on the card too
  failed: boolean
  // tried again RETRY later
  retry: boolean
  // a notification: only for the first failure in a row
  notify: boolean
}

// What an automatic backup's answer means. Another backup of the project running ("busy": the system's
// scheduled one, say) is no failure and is tried again soon; nor is a run Force stop ended. A failure is tried
// again soon too, and only the first of a row makes a notification.
export function autoOutcome(
  r: { ok: boolean; code?: string; message?: string; data?: Record<string, unknown> },
  failingBefore: boolean,
  forceStopped: boolean
): AutoOutcome {
  if (r.ok) {
    const text = r.data?.made ? `Backed up the live release (${String(r.data.name)}).` : 'The live release has not changed since its last backup.'
    return { text, style: 'ok', failed: false, retry: false, notify: false }
  }
  if (forceStopped) return { text: `The backup: ${r.message ?? ''}`, style: 'warn', failed: false, retry: false, notify: false }
  if (r.code === 'busy') {
    return { text: 'Another backup of it is running: the app tries again in 15 minutes.', style: 'warn', failed: false, retry: true, notify: false }
  }
  return { text: `The backup failed: ${r.message ?? 'the engine gave no reason'}`, style: 'err', failed: true, retry: true, notify: !failingBefore }
}

// the time of a backup's "made" ("YYYY-MM-DDTHH:MM:SSZ"), in milliseconds; null when there is none
export function madeAt(made: string | undefined): number | null {
  const t = Date.parse(String(made || ''))
  return Number.isFinite(t) ? t : null
}

// what is wrong with backup_max_size as typed ('' when nothing): the engine's own rule, said while it is typed
export function sizeProblem(text: string): string {
  const t = text.trim()
  if (!t) return ''
  return /^\d+(\.\d+)?\s*(bytes|byte|b|kb|mb|gb|tb)$/i.test(t) ? '' : 'A size with its unit, such as 10 GB or 500 MB.'
}
