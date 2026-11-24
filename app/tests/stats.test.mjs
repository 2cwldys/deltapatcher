// The Statistics card's view (src/renderer/src/store/stats.ts) of the engine's statistics document.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { bars, readStats, shares, when } from '../src/renderer/src/store/stats.ts'

const day = (date, checks, downloads, bytes, players) => ({ date, checks, downloads, bytes, players })

const DOC = {
  format: 'deltapatcher-stats',
  version: 1,
  project: { project: 'Default', file: 'deltapatcher.ini' },
  made: '2026-11-24T12:00:00Z',
  days: 3,
  sections: {
    releases: {
      store: 'D:\\stores\\game-1',
      current: { name: 'My Game', version: '1.2', published: '2026-11-20T10:00:00Z', files: 120, bytes: 5000, blobs: 118 },
      change: { from: { name: 'My Game', version: '1.1', published: '2026-11-10T10:00:00Z' }, new: 3, changed: 2, removed: 1, download_bytes: 900 },
      store_files: 130,
      store_bytes: 7000,
      history: [],
      history_total: 5
    },
    downloads: {
      days: [day('2026-11-22', 4, 1, 100, 2), day('2026-11-23', 0, 0, 0, 0), day('2026-11-24', 8, 3, 300, 5)],
      total: { checks: 12, downloads: 4, bytes: 400, players: 6 },
      releases: [{ release: '1.2', players: 3 }, { release: null, players: 2 }],
      releases_more: 1,
      lines: 20,
      unreadable: 2,
      counted: 18,
      log: '/var/log/nginx/game.log',
      read_from: 'server',
      path: '/'
    }
  },
  problems: {}
}

test('the release, what it changed, and the store', () => {
  const v = readStats(DOC)
  assert.equal(v.ok, true)
  assert.deepEqual(v.release, DOC.sections.releases.current)
  assert.deepEqual(v.change, { from: '1.1', new: 3, changed: 2, removed: 1, download: 900 })
  assert.deepEqual(v.store, { bytes: 7000, files: 130, releases: 5 })
  assert.equal(when('2026-11-20T10:00:00Z'), '2026-11-20 10:00:00 UTC')
})

test('each day a bar, as a share of the busiest day', () => {
  const v = readStats(DOC)
  const checks = v.downloads.rows.find((r) => r.key === 'checks')
  assert.equal(checks.max, 8)
  assert.deepEqual(checks.bars.map((b) => b.height), [0.5, 0, 1])
  assert.deepEqual(v.downloads.rows.map((r) => r.label), ['Update checks', 'Downloads', 'Players'])
  assert.equal(v.downloads.first, '2026-11-22')
  assert.equal(v.downloads.last, '2026-11-24')
  assert.deepEqual(bars([day('2026-11-24', 0, 0, 0, 0)], 'players').bars, [{ date: '2026-11-24', value: 0, height: 0 }])
})

test('the releases players are on: unknown for an older updater, and the rest together', () => {
  const v = readStats(DOC)
  assert.deepEqual(v.downloads.releases.map((r) => [r.label, r.players]), [['1.2', 3], ['unknown', 2], ['others', 1]])
  assert.equal(v.downloads.releases[0].share, 0.5)
  assert.deepEqual(shares([], 0), [])
})

test('a section that is missing says why; another document is not read', () => {
  const doc = { ...DOC, sections: {}, problems: { releases: 'nothing is published yet', downloads: 'access_log is not set' } }
  const v = readStats(doc)
  assert.equal(v.ok, true)
  assert.equal(v.release, undefined)
  assert.equal(v.downloads, undefined)
  assert.equal(v.releasesProblem, 'nothing is published yet')
  assert.equal(v.downloadsProblem, 'access_log is not set')
  assert.equal(readStats({ ...DOC, version: 2 }).ok, false)
  assert.equal(readStats(null).ok, false)
  assert.equal(readStats({ ...DOC, sections: { releases: { ...DOC.sections.releases, change: null } } }).change, undefined)
})
