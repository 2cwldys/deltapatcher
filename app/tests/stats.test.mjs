// The Statistics card's view (src/renderer/src/store/stats.ts) of the engine's statistics document.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { bars, players, readStats, shares, statsCounter, statsSummary, when } from '../src/renderer/src/store/stats.ts'

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
  // releases named like the rows of their own: every row keeps a key of its own
  const named = shares([{ release: 'unknown', players: 4 }, { release: null, players: 3 }, { release: 'others', players: 2 }], 1)
  assert.deepEqual(named.map((r) => r.label), ['unknown', 'unknown', 'others', 'others'])
  assert.deepEqual(named.map((r) => r.key), ['release:unknown', 'unknown', 'release:others', 'others'])
})

test('players the engine estimated are "about"', () => {
  assert.equal(readStats(DOC).downloads.about, false)
  const doc = { ...DOC, sections: { ...DOC.sections, downloads: { ...DOC.sections.downloads, players_estimated: true } } }
  assert.equal(readStats(doc).downloads.about, true)
  assert.equal(players(120345, true), 'about 120345')
  assert.equal(players(6, false), '6')
})

test('a count that ends after the project changed is kept for its project, never shown on another', async () => {
  let onScreen = 'B.ini'
  const waiting = []
  const run = (file) => new Promise((resolve) => waiting.push({ file, resolve }))
  const shown = []
  const counter = statsCounter(run, () => onScreen, (card) => shown.push(card))
  const docOf = (version) => ({ ...DOC, sections: { releases: { ...DOC.sections.releases, current: { ...DOC.sections.releases.current, version } } } })
  const b = counter.count()                                   // B counted first
  waiting.shift().resolve({ ok: true, data: docOf('2.0') })
  await b
  assert.equal(shown.at(-1).view.release.version, '2.0')
  onScreen = 'A.ini'                                          // A counted; while it counts, B is shown again
  const slow = counter.count()
  assert.equal(shown.at(-1).loading, true)
  onScreen = 'B.ini'
  const before = shown.length
  const a = waiting.shift()
  assert.equal(a.file, 'A.ini')
  a.resolve({ ok: true, data: docOf('1.0') })
  await slow
  assert.equal(shown.length, before, "A's numbers never went to B's card")
  assert.equal(counter.kept.get('A.ini').release.version, '1.0', 'kept for A')
  assert.equal(counter.shown('B.ini').view.release.version, '2.0')
  assert.equal(counter.shown('A.ini').view.release.version, '1.0')
  onScreen = 'A.ini'                                          // two counts of one project: the newer one is shown
  const older = counter.count()
  const newer = counter.count()
  const [first, second] = waiting.splice(0, 2)
  second.resolve({ ok: true, data: docOf('1.2') })
  first.resolve({ ok: true, data: docOf('1.1') })
  await Promise.all([older, newer])
  assert.equal(shown.at(-1).view.release.version, '1.2')
  assert.equal(counter.kept.get('A.ini').release.version, '1.2', 'the older answer, later, did not replace it')
  const failing = counter.count()                             // a failure: on its own card, with what was counted before
  waiting.shift().resolve({ ok: false, message: 'the access log could not be read' })
  await failing
  assert.deepEqual([shown.at(-1).error, shown.at(-1).view.release.version, shown.at(-1).loading],
    ['the access log could not be read', '1.2', false])
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

test('folded, the card says the checks and players of the period, or what stands in the way', () => {
  const card = (view, extra = {}) => ({ view, error: '', loading: false, ...extra })
  const v = readStats(DOC)
  assert.equal(statsSummary(card(v), '/var/log/nginx/game.log'), '12 update checks and 6 players in the last 3 days')
  const about = readStats({ ...DOC, sections: { ...DOC.sections, downloads: { ...DOC.sections.downloads, players_estimated: true } } })
  assert.equal(statsSummary(card(about), 'x'), '12 update checks and about 6 players in the last 3 days')
  const total = { checks: 1, downloads: 0, bytes: 0, players: 1 }
  const one = readStats({ ...DOC, sections: { ...DOC.sections, downloads: { ...DOC.sections.downloads, total } } })
  assert.equal(statsSummary(card(one), 'x'), '1 update check and 1 player in the last 3 days')
  const noLog = readStats({ ...DOC, sections: { releases: DOC.sections.releases }, problems: { downloads: 'access_log is not set' } })
  assert.equal(statsSummary(card(noLog), ''), 'Release 1.2; players are counted once the access log is named')
  assert.equal(statsSummary(card(noLog), '/var/log/x.log'), 'Release 1.2; players could not be counted: open it to see why')
  assert.equal(statsSummary(card(null, { loading: true }), ''), 'Counting...')
  assert.equal(statsSummary(card(null), ''), '')
  assert.match(statsSummary(card(v, { error: 'the engine stopped' }), 'x'), /could not be counted/)
})
