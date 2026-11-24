// The Backups card and the app's automatic backups: the engine's backup list as the card shows it, the
// question a restore asks, when a project is due a backup, and the size limit as typed. Nothing here uses the
// window, so a test can run it as it is.

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

export interface BackupList {
  folder: string
  backups: BackupInfo[]
  bytes: number
}

export interface BackupRow {
  name: string
  made: string
  release: string
  files: number
  bytes: number
}

export function releaseWords(r: Partial<ReleaseShort> | null | undefined): string {
  return r ? `${r.name || '(unnamed)'} ${r.version ?? ''}`.trim() : 'nothing'
}

// the backups, the newest first, as rows
export function backupRows(list: BackupList | null | undefined): BackupRow[] {
  return (list?.backups ?? []).map((b) => ({
    name: b.name,
    made: String(b.made || '').replace('T', ' ').replace('Z', ' UTC'),
    release: releaseWords(b.release),
    files: b.release?.files ?? 0,
    bytes: b.release?.bytes ?? 0
  }))
}

// what a restore asks first, from what the engine says it replaces
export function restoreQuestion(d: { backup?: BackupInfo; live?: ReleaseShort | null; destination?: string }): { title: string; body: string } {
  const name = d.backup?.name ?? ''
  return {
    title: `Restore the backup of ${name}?`,
    body:
      `${d.destination ?? ''}\n\n` +
      `Players get ${releaseWords(d.backup?.release)} from there from then on, instead of ${releaseWords(d.live)}.\n` +
      'Its blobs go first and its manifest last, as a release goes.\n' +
      'Nothing there is removed.'
  }
}

// whether a project is due an automatic backup: backup_every_hours (0 or less: never) since the last one
export function backupDue(now: number, last: number | null, hours: unknown): boolean {
  const h = Number(hours)
  if (!Number.isFinite(h) || h <= 0) return false
  return last === null || now - last >= h * 60 * 60 * 1000
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
