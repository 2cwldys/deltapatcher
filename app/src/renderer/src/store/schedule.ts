// When DeltaPatcher checks every project by itself: once when it starts, then every check_every_hours hours
// while it runs (0, or anything that is not a number of hours: only when it starts). A check that comes due
// while another run goes waits until that run has ended.

// how often the window looks whether a check is due
export const TICK = 60 * 1000

// last: when the last automatic check started (null: none yet)
export function checkDue(now: number, last: number | null, hours: unknown): boolean {
  if (last === null) return true
  const h = Number(hours)
  if (!Number.isFinite(h) || h <= 0) return false
  return now - last >= h * 60 * 60 * 1000
}

// a run that is going: Update all or Check all, one project's update or check, the job of the Update or
// Release page (an update, a release, a rollback, a backup), or a backup the app makes by itself
export function anotherRun(s: { busy: string | null; allJob: unknown; runs: Record<string, { state: string }>; backupJob?: string | null }): boolean {
  return Boolean(s.busy) || Boolean(s.allJob) || Boolean(s.backupJob) ||
    Object.values(s.runs).some((r) => ['waiting', 'checking', 'updating'].includes(r.state))
}

// The automatic checks: the function it returns is called every TICK with the time and check_every_hours,
// and starts a check when one is due. start() says 'busy' when another run goes: the check then waits for a
// later tick. 'nothing' (no project to check) counts as done.
export function scheduler(start: () => 'started' | 'busy' | 'nothing'): (now: number, hours: unknown) => void {
  let last: number | null = null
  return (now, hours) => {
    if (!checkDue(now, last, hours)) return
    if (start() !== 'busy') last = now
  }
}
