// The Backups card's view and the app's automatic backups (src/renderer/src/store/backups.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  autoOutcome, backupDue, backupRows, backupSummary, madeAt, nextBackup, RETRY, restoreQuestion, scheduledLine, sizeProblem
} from '../src/renderer/src/store/backups.ts'

const HOUR = 60 * 60 * 1000
const LIST = {
  folder: 'D:\\backups\\deltapatcher',
  bytes: 9000,
  backups: [
    { name: '2026-11-24', made: '2026-11-24T03:12:00Z', from: 'deploy@game.example.com:22:/var/www/game',
      release: { name: 'My Game', version: '1.2', files: 41, bytes: 2000 } },
    { name: '2026-11-23', made: '2026-11-23T03:12:00Z', from: 'deploy@game.example.com:22:/var/www/game',
      release: { name: '', version: '1.1', files: 40, bytes: 1900 } }
  ]
}

test('the backups as rows, the newest first', () => {
  const rows = backupRows(LIST)
  assert.deepEqual(rows.map((r) => [r.name, r.release, r.files, r.bytes]),
    [['2026-11-24', 'My Game 1.2', 41, 2000], ['2026-11-23', '(unnamed) 1.1', 40, 1900]])
  assert.equal(rows[0].made, '2026-11-24 03:12:00 UTC')
  assert.deepEqual(backupRows(null), [])
})

test('a restore says what it replaces, and that nothing else is removed', () => {
  const q = restoreQuestion({ backup: LIST.backups[1], live: { name: 'My Game', version: '1.2', files: 41, bytes: 2000 },
    destination: 'deploy@game.example.com:22:/var/www/game' })
  assert.equal(q.title, 'Restore the backup of 2026-11-23?')
  assert.match(q.body, /^deploy@game\.example\.com:22:\/var\/www\/game\n\n/)
  assert.match(q.body, /Players get \(unnamed\) 1\.1 from there from then on, instead of My Game 1\.2\./)
  assert.match(q.body, /Nothing there is removed, except a file of the same name and the wrong size, which is replaced\.$/)
  // nothing released there: said so
  assert.match(restoreQuestion({ backup: LIST.backups[0], live: null, destination: 'D:\\www' }).body, /instead of nothing\./)
  // a release there that cannot be read: said so, never "instead of nothing"
  const unread = restoreQuestion({ backup: LIST.backups[0], live: null, live_problem: 'the manifest is damaged', destination: 'D:\\www' })
  assert.match(unread.body, /The release there now could not be read \(the manifest is damaged\)\./)
  assert.doesNotMatch(unread.body, /instead of/)
})

test('the folded list in one line: how many, the newest, the size, and what makes them', () => {
  assert.equal(backupSummary(LIST, 24, '2026-11-24', '8.8 KB'), '2 backups, the newest today, 8.8 KB; every 24 hours')
  assert.equal(backupSummary(LIST, 1, '2026-11-25', '8.8 KB'), '2 backups, the newest yesterday, 8.8 KB; every hour')
  assert.equal(backupSummary({ ...LIST, backups: [LIST.backups[1]] }, 0, '2026-12-01', '4 KB'),
    '1 backup, the newest 2026-11-23, 4 KB; only when asked')
  const scheduled = { time: '2026-11-24T03:17:05Z', schedule: 'daily', ok: true, made: true, name: '2026-11-24', code: '', message: '' }
  assert.equal(backupSummary({ ...LIST, scheduled }, 0, '2026-11-24', '8.8 KB'), '2 backups, the newest today, 8.8 KB; daily by the system')
  assert.equal(backupSummary({ ...LIST, backups: [] }, 24, '2026-11-24', '0 bytes'), 'No backups yet; every 24 hours')
})

test("the system scheduler's last run, in a line", () => {
  assert.equal(scheduledLine(null), '')
  assert.equal(scheduledLine({ time: '2026-11-24T03:17:05Z', schedule: 'daily', ok: true, made: true, name: '2026-11-24', code: '',
    message: 'made the backup of 2026-11-24 (My Game 1.2)' }),
  "The system's last scheduled run: 2026-11-24 03:17:05 UTC: made the backup of 2026-11-24 (My Game 1.2)")
  assert.match(scheduledLine({ time: '2026-11-24T04:17:05Z', schedule: 'daily', ok: false, made: false, name: '', code: 'key-locked',
    message: '' }), /UTC: it failed$/)
})

test('an automatic backup is due every backup_every_hours, never with 0', () => {
  assert.equal(backupDue(10 * HOUR, null, 24), true)                  // none yet
  assert.equal(backupDue(10 * HOUR, 0, 24), false)
  assert.equal(backupDue(24 * HOUR, 0, 24), true)
  assert.equal(backupDue(24 * HOUR, 0, '6'), true)
  for (const hours of [0, -1, '', 'often', undefined]) assert.equal(backupDue(1000 * HOUR, null, hours), false)
  assert.equal(madeAt('2026-11-24T03:12:00Z'), Date.UTC(2026, 10, 24, 3, 12, 0))
  assert.equal(madeAt(''), null)
  assert.equal(nextBackup(24, null, null, false), 0)                  // none yet: now
  assert.equal(nextBackup(24, 5 * HOUR, 2 * HOUR, false), 29 * HOUR)   // from the newer of the backup and the try
  for (const hours of [0, -1, '', 'often', undefined]) assert.equal(nextBackup(hours, null, null, true), null)
})

test('after a failure, or another backup running, the next try comes 15 minutes later', () => {
  assert.equal(RETRY, 15 * 60 * 1000)
  assert.equal(nextBackup(24, 0, 10 * HOUR, true), 10 * HOUR + RETRY)
  assert.equal(nextBackup(24, 0, 10 * HOUR, false), 34 * HOUR)        // a try that worked: a day later
  assert.equal(nextBackup(24, 11 * HOUR, 10 * HOUR, true), 35 * HOUR) // a backup made since (the scheduler's): counts from it
  // a failure is no backup: tried again and again, each 15 minutes after the last try
  let tried = 0
  for (let i = 0; i < 3; i++) tried = nextBackup(1, null, tried, true)
  assert.equal(tried, 3 * RETRY)
})

test('a busy lock is no failure; a failure is said, the first of a row in a notification', () => {
  const busy = autoOutcome({ ok: false, code: 'busy', message: 'another backup of this project is running' }, false, false)
  assert.deepEqual([busy.failed, busy.notify, busy.retry, busy.style], [false, false, true, 'warn'])
  const failed = autoOutcome({ ok: false, code: 'read-failed', message: 'the server could not be reached' }, false, false)
  assert.deepEqual([failed.failed, failed.notify, failed.retry, failed.style], [true, true, true, 'err'])
  assert.equal(failed.text, 'The backup failed: the server could not be reached')
  assert.equal(autoOutcome({ ok: false, code: 'read-failed', message: 'again' }, true, false).notify, false)
  const stopped = autoOutcome({ ok: false, code: 'force-stopped', message: 'Stopped by Force stop.' }, false, true)
  assert.deepEqual([stopped.failed, stopped.notify, stopped.retry], [false, false, false])
  const made = autoOutcome({ ok: true, data: { made: true, name: '2026-11-24' } }, true, false)
  assert.deepEqual([made.text, made.failed, made.retry], ['Backed up the live release (2026-11-24).', false, false])
  assert.equal(autoOutcome({ ok: true, data: { made: false } }, false, false).text, 'The live release has not changed since its last backup.')
})

test('the size limit as typed', () => {
  for (const ok of ['', '10 GB', '500mb', '1.5 TB', '2048 bytes']) assert.equal(sizeProblem(ok), '', ok)
  for (const bad of ['10', 'lots', 'GB', '-1 GB', '10 parsecs']) assert.notEqual(sizeProblem(bad), '', bad)
})
