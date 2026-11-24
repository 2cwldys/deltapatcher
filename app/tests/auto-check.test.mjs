// The automatic checks (src/renderer/src/store/schedule.ts): at the start, then every check_every_hours hours,
// never while another run goes.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { anotherRun, checkDue, scheduler } from '../src/renderer/src/store/schedule.ts'

const HOUR = 60 * 60 * 1000
const idle = { busy: null, allJob: null, runs: {} }

test('a check at the start, then every check_every_hours hours', () => {
  assert.equal(checkDue(0, null, 1), true)
  assert.equal(checkDue(HOUR - 1, 0, 1), false)
  assert.equal(checkDue(HOUR, 0, 1), true)
  assert.equal(checkDue(3 * HOUR - 1, 0, 3), false)
  assert.equal(checkDue(3 * HOUR, 0, '3'), true)
  assert.equal(checkDue(1000 * HOUR, 0, 999), true)
})

test('0, or anything that is not a number of hours: only at the start', () => {
  for (const hours of [0, -2, '', 'often', undefined, null, NaN]) {
    assert.equal(checkDue(0, null, hours), true, String(hours))
    assert.equal(checkDue(1000 * HOUR, 0, hours), false, String(hours))
  }
})

test('another run: Update all, one project, or the Update or Release page', () => {
  assert.equal(anotherRun(idle), false)
  assert.equal(anotherRun({ ...idle, busy: 'Release' }), true)
  assert.equal(anotherRun({ ...idle, allJob: { jobId: 'all-1', check: false, total: 2, done: 0 } }), true)
  for (const state of ['waiting', 'checking', 'updating']) assert.equal(anotherRun({ ...idle, runs: { a: { state } } }), true)
  for (const state of ['done', 'failed']) assert.equal(anotherRun({ ...idle, runs: { a: { state } } }), false)
})

test('a check that comes due while another run goes starts once it has ended', () => {
  let busy = false
  const started = []
  let now = 0
  const tick = scheduler(() => {
    if (busy) return 'busy'
    started.push(now)
    return 'started'
  })
  tick(now, 1)                                     // the start
  now = HOUR - 60000
  tick(now, 1)                                     // not yet
  busy = true
  now = HOUR
  tick(now, 1)                                     // due, but a release is going
  now = HOUR + 60000
  tick(now, 1)
  busy = false
  now = HOUR + 120000
  tick(now, 1)                                     // the release ended: the check starts
  now = 2 * HOUR
  tick(now, 1)                                     // an hour after that one, not after the due time
  assert.deepEqual(started, [0, HOUR + 120000])
  now = 2 * HOUR + 120000
  tick(now, 1)
  assert.deepEqual(started, [0, HOUR + 120000, 2 * HOUR + 120000])
})

test('the setting changed while it runs counts from the last check; no project to check counts as checked', () => {
  const started = []
  let result = 'nothing'
  const tick = scheduler(() => {
    started.push(result)
    return result
  })
  tick(0, 0)
  tick(5 * HOUR, 0)                                // 0: only at the start
  assert.deepEqual(started, ['nothing'])
  result = 'started'
  tick(5 * HOUR, 4)                                // turned to 4 hours: 5 have gone by since the start
  tick(6 * HOUR, 4)
  assert.deepEqual(started, ['nothing', 'started'])
})
