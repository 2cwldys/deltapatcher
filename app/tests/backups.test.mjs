// The Backups card's view and the app's automatic backups (src/renderer/src/store/backups.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { backupDue, backupRows, madeAt, restoreQuestion, sizeProblem } from '../src/renderer/src/store/backups.ts'

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

test('a restore says what it replaces and that nothing is removed', () => {
  const q = restoreQuestion({ backup: LIST.backups[1], live: { name: 'My Game', version: '1.2', files: 41, bytes: 2000 },
    destination: 'deploy@game.example.com:22:/var/www/game' })
  assert.equal(q.title, 'Restore the backup of 2026-11-23?')
  assert.match(q.body, /^deploy@game\.example\.com:22:\/var\/www\/game\n\n/)
  assert.match(q.body, /Players get \(unnamed\) 1\.1 from there from then on, instead of My Game 1\.2\./)
  assert.match(q.body, /Nothing there is removed\.$/)
  assert.match(restoreQuestion({ backup: LIST.backups[0], live: null, destination: 'D:\\www' }).body, /instead of nothing\./)
})

test('an automatic backup is due every backup_every_hours, never with 0', () => {
  assert.equal(backupDue(10 * HOUR, null, 24), true)                  // none yet
  assert.equal(backupDue(10 * HOUR, 0, 24), false)
  assert.equal(backupDue(24 * HOUR, 0, 24), true)
  assert.equal(backupDue(24 * HOUR, 0, '6'), true)
  for (const hours of [0, -1, '', 'often', undefined]) assert.equal(backupDue(1000 * HOUR, null, hours), false)
  assert.equal(madeAt('2026-11-24T03:12:00Z'), Date.UTC(2026, 10, 24, 3, 12, 0))
  assert.equal(madeAt(''), null)
})

test('the size limit as typed', () => {
  for (const ok of ['', '10 GB', '500mb', '1.5 TB', '2048 bytes']) assert.equal(sizeProblem(ok), '', ok)
  for (const bad of ['10', 'lots', 'GB', '-1 GB', '10 parsecs']) assert.notEqual(sizeProblem(bad), '', bad)
})
