// Force stop (src/shared/forceStop.ts, src/renderer/src/store/schedule.ts, src/renderer/src/store/modal.ts):
// what still runs while it is on, the automatic checks starting over once it is lifted, and no dialog while it
// holds.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allowedWhileStopped, FORCE_STOPPED } from '../src/shared/forceStop.ts'
import { restartSchedules, scheduler } from '../src/renderer/src/store/schedule.ts'
import { useModal } from '../src/renderer/src/store/modal.ts'

const HOUR = 60 * 60 * 1000

test('only drawing the window and keeping settings run while it is on', () => {
  for (const args of [['version'], ['projects'], ['backup', 'list', '--project=a.ini'], ['config', 'get', '--project=a.ini'],
    ['config', 'set', 'auto_update=no', '--project=a.ini']]) {
    assert.equal(allowedWhileStopped(args), true, args.join(' '))
  }
  for (const args of [
    ['update', '--project=a.ini'], ['update', '--all'], ['update', '--all', '--check'], ['rollback', '--project=a.ini'],
    ['publish'], ['deploy'], ['serve', '--port=8000'], ['self-update', '--check'], ['self-update', '--app=setup'],
    ['project', 'import', 'game.deltapatch', '--yes'], ['project', 'export', 'a.ini', '--out=x.html'], ['project', 'new', 'A'],
    ['project', 'remove', '--all', '--yes'], ['reset', '--yes'], ['sign', 'status'], ['ssh', 'status'], ['stats', '--days=30'],
    ['backup'], ['backup', 'now'], ['backup', 'restore', '2026-11-24', '--yes'], ['backup', 'prune'], ['backup', 'schedule', 'daily'],
    ['config'], ['config', '--show'], []
  ]) {
    assert.equal(allowedWhileStopped(args), false, args.join(' ') || '(no command)')
  }
  assert.equal(FORCE_STOPPED, 'force-stopped')
})

test('lifted: nothing starts at that moment, the next check comes a whole interval later', () => {
  let started = 0
  let stopped = false
  const tick = scheduler(() => {
    if (stopped) return 'busy'
    started++
    return 'started'
  })
  tick(0, 1)
  assert.equal(started, 1, 'the check at the start')
  stopped = true
  tick(HOUR, 1)
  tick(2 * HOUR, 1)
  assert.equal(started, 1, 'due, but Force stop is on')
  stopped = false
  restartSchedules(2 * HOUR + 30000)
  tick(2 * HOUR + 60000, 1)
  tick(3 * HOUR + 29000, 1)
  assert.equal(started, 1, 'not at the lift, nor before an hour has gone by')
  tick(3 * HOUR + 30000, 1)
  assert.equal(started, 2, 'an hour after the lift')
  tick(4 * HOUR + 29000, 1)
  assert.equal(started, 2)
  tick(4 * HOUR + 30000, 1)
  assert.equal(started, 3, 'then every hour, as before')
})

test('a window that opened stopped makes its first check an interval after the lift', () => {
  let started = 0
  let stopped = true
  const tick = scheduler(() => {
    if (stopped) return 'busy'
    started++
    return 'started'
  })
  tick(0, 1)
  assert.equal(started, 0)
  stopped = false
  restartSchedules(10 * HOUR)
  tick(10 * HOUR, 1)
  assert.equal(started, 0)
  tick(11 * HOUR, 1)
  assert.equal(started, 1)
  // check_every_hours 0 (only when DeltaPatcher starts): that start was stopped, so no check until the next start
  const once = scheduler(() => {
    started++
    return 'started'
  })
  restartSchedules(12 * HOUR)
  once(12 * HOUR + 1, 0)
  once(100 * HOUR, 0)
  assert.equal(started, 1)
})

test('no dialog while it is on: the open one answers as cancelled, a new one at once', async () => {
  const confirm = useModal.getState().open({ kind: 'confirm', title: 'Remove A?', body: '', confirm: 'Remove' })
  assert.equal(useModal.getState().spec?.kind, 'confirm')
  useModal.getState().block(true)
  assert.equal(await confirm, false)
  assert.equal(useModal.getState().spec, null)
  assert.equal(await useModal.getState().open({ kind: 'passphrase', title: 'Sign', body: '', twice: false, minLength: 0 }), null)
  assert.equal(await useModal.getState().open({ kind: 'prompt', title: 'Name', body: '', value: 'A', confirm: 'OK' }), null)
  assert.equal(await useModal.getState().open({ kind: 'info', title: 'Not added', body: '' }), undefined)
  assert.equal(await useModal.getState().open({ kind: 'hostkeys', host: 'h', keys: [] }), false)
  assert.equal(useModal.getState().spec, null, 'none of them was shown')
  useModal.getState().block(false)
  const later = useModal.getState().open({ kind: 'confirm', title: 'Remove A?', body: '', confirm: 'Remove' })
  assert.equal(useModal.getState().spec?.kind, 'confirm', 'lifted: dialogs open again')
  useModal.getState().close(true)
  assert.equal(await later, true)
})
