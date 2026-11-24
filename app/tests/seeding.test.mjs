// Seeding in the window (src/renderer/src/store/seeding.ts): when the seed run starts and starts again, Force stop
// turning seeding off, each project's line from the run's events, the import dialog's words about torrents, and the
// torrent settings checked as they are typed.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  afterRun, hasTorrentId, MAX_SEEDS, NEEDS_SIGNING, portProblem, projectSeedLine, rate, REFUSED_WHY, refusedLine, RESTART_KEYS,
  RETRY, SEED_IT, SEED_KEYS, seedable, seedingCount, seedLine, seedOffArgs, seedsProblem, STALE, STEADY, torrentMark,
  torrentNameProblem, torrentsRefused, torrentSwitches, wantsSeeding, withRow
} from '../src/renderer/src/store/seeding.ts'
import { allowedWhileStopped, FORCE_STOPPED } from '../src/shared/forceStop.ts'

const shared = { file: 'D:\\dp\\projects\\Shared.ini', torrent: true, seed: true }
const plain = { file: 'D:\\dp\\projects\\Plain.ini', torrent: false, seed: false }
const row = {
  project: 'Shared', file: shared.file, folder: 'D:\\Games\\Shared', kind: 'folder', state: 'seeding', why: '',
  release: 'Shared 1.0', peers: 3, upload_rate: 120 * 1024, sent: 4_500_000
}

test('the seed run starts when a project shared by torrent is set to seed, torrents are allowed and Force stop is off', () => {
  assert.equal(wantsSeeding([plain, shared], 'allow', false), true)
  assert.equal(wantsSeeding([plain, shared], 'refuse', false), false, 'torrents refused')
  assert.equal(wantsSeeding([plain, shared], 'allow', true), false, 'Force stop is on')
  assert.equal(wantsSeeding([plain], 'allow', false), false, 'nothing set to seed')
  assert.equal(wantsSeeding([{ ...shared, seed: false }], 'allow', false), false, 'shared, but seed = no')
  assert.equal(wantsSeeding([{ ...shared, torrent: false }], 'allow', false), false, 'seed = yes, but not shared by torrent')
  assert.equal(wantsSeeding([{ ...shared, missing: true }], 'allow', false), false, 'its settings file is gone')
  assert.deepEqual(seedable([plain, shared, { ...shared, file: 'b.ini', missing: true }]), [shared])
  // only allow lets BitTorrent in, as the engine reads it; not read yet: the default, allow
  for (const v of ['refuse', 'Refuse', '', 'no', 'alow']) assert.equal(torrentsRefused(v), true, v)
  for (const v of ['allow', ' Allow ', undefined, null]) assert.equal(torrentsRefused(v), false, String(v))
})

test('a seed setting may start a run; the port, the upload limit and port mapping start it again', () => {
  assert.deepEqual(SEED_KEYS, ['seed', 'torrent', 'torrent_id', 'torrents'])
  assert.deepEqual(RESTART_KEYS, ['torrent_port', 'seed_upload_kbps', 'torrent_port_mapping'])
})

test('Force stop turns seed off for every project that had it, with config set, which runs while the stop holds', () => {
  const projects = [shared, plain, { ...shared, file: 'D:\\dp\\Gone.ini', missing: true }, { ...shared, file: 'D:\\dp\\Two.ini' }]
  const args = seedOffArgs(projects)
  assert.deepEqual(args, [
    ['config', 'set', 'seed=no', `--project=${shared.file}`],
    ['config', 'set', 'seed=no', '--project=D:\\dp\\Two.ini']
  ])
  for (const a of args) {
    assert.equal(allowedWhileStopped(a), true, a.join(' '))
    // each names its project: the window's withProject() adds no other
    assert.ok(a.some((x) => x.startsWith('--project=')))
  }
  assert.deepEqual(seedOffArgs([plain]), [])
  // the seed run itself, and the BitTorrent self-test (it makes a session), do not run while it holds
  assert.equal(allowedWhileStopped(['seed', '--all']), false)
  assert.equal(allowedWhileStopped(['version', '--torrent']), false)
  assert.equal(allowedWhileStopped(['version']), true)
})

test("a project's line from the run's events", () => {
  assert.equal(seedLine(row), 'Seeding: 3 peers, 120 KB/s up')
  assert.equal(seedLine({ ...row, peers: 1, upload_rate: 0 }), 'Seeding: 1 peer, 0 KB/s up')
  assert.equal(seedLine({ ...row, peers: 0, upload_rate: 3 * 1024 * 1024 }), 'Seeding: 0 peers, 3.0 MB/s up')
  assert.equal(seedLine({ ...row, state: 'paused', why: 'an update is changing its folder' }),
    'Seeding paused while an update changes its folder.')
  assert.equal(seedLine({ ...row, state: 'waiting', why: 'its folder keeps no release to seed yet: update it first' }),
    'Not seeded: its folder keeps no release to seed yet: update it first.')
  assert.equal(seedLine({ ...row, state: 'waiting', why: '' }), 'Getting ready to seed.')
  assert.equal(seedLine({ ...row, state: 'stopped', why: 'seed is off for it now' }), 'Not seeded: seed is off for it now.')
  assert.equal(rate(999 * 1024), '999 KB/s')
  assert.equal(rate(1000 * 1024), '1.0 MB/s')
  assert.equal(rate(-5), '0 KB/s')
})

test('a row on the Projects page: the run says it, or why the project is not seeded', () => {
  const idle = { refused: false, running: false, why: '', stopped: false }
  assert.equal(projectSeedLine(shared, row, { ...idle, running: true }), 'Seeding: 3 peers, 120 KB/s up')
  assert.equal(projectSeedLine(shared, undefined, { ...idle, running: true }), 'Getting ready to seed.')
  assert.equal(projectSeedLine(shared, undefined, { ...idle, refused: true }), REFUSED_WHY)
  assert.equal(REFUSED_WHY, 'Not seeding: torrents are refused in the settings (Settings, Torrents).')
  assert.equal(projectSeedLine(shared, undefined, { ...idle, why: 'Not seeding: this DeltaPatcher has no BitTorrent support.' }),
    'Not seeding: this DeltaPatcher has no BitTorrent support.')
  assert.equal(projectSeedLine(shared, undefined, idle), '')
  // a project not set to seed (a moment ago it was) or not shared, gone, or Force stop on: no line
  assert.equal(projectSeedLine({ ...shared, seed: false }, row, { ...idle, running: true }), '')
  assert.equal(projectSeedLine(plain, undefined, { ...idle, refused: true }), '')
  assert.equal(projectSeedLine({ ...shared, missing: true }, row, idle), '')
  assert.equal(projectSeedLine(shared, row, { ...idle, stopped: true }), '')
})

test("the run's rows: one a project, the newest; a project the run no longer sends goes; the tray's count", () => {
  let rows = withRow({}, row, 1000)
  rows = withRow(rows, { ...row, file: 'b.ini', state: 'waiting' }, 2000)
  rows = withRow(rows, { ...row, peers: 5 }, 6000)
  assert.deepEqual(Object.keys(rows).sort(), ['b.ini', shared.file].sort())
  assert.equal(rows[shared.file].peers, 5)
  assert.equal(seedingCount(rows), 1)
  rows = withRow(rows, { ...row, peers: 6 }, 2000 + STALE)
  assert.deepEqual(Object.keys(rows), [shared.file], 'b.ini sent nothing for too long')
  assert.equal(seedingCount({}), 0)
})

test('after a run: Force stop ends it for good, the window starts it again, some ends wait for a seed setting', () => {
  const c = { restarting: false, stopped: false, wanted: true, refused: false, ranFor: 1000, retries: 0 }
  assert.deepEqual(afterRun({ ok: false, code: FORCE_STOPPED }, c), { next: 'nothing' })
  assert.deepEqual(afterRun({ ok: true }, { ...c, stopped: true }), { next: 'nothing' })
  assert.deepEqual(afterRun({ ok: true }, { ...c, restarting: true }), { next: 'start' })
  assert.deepEqual(afterRun({ ok: false, code: 'engine-stopped' }, { ...c, restarting: true }), { next: 'start' })
  assert.deepEqual(afterRun({ ok: true }, { ...c, restarting: true, wanted: false }), { next: 'nothing' })
  assert.deepEqual(afterRun({ ok: false, code: 'torrents-refused' }, c),
    { next: 'wait', why: 'Not seeding: torrents are refused in the settings (Settings, Torrents).' })
  assert.deepEqual(afterRun({ ok: false, code: 'no-torrent-support' }, c),
    { next: 'wait', why: 'Not seeding: this DeltaPatcher has no BitTorrent support.' })
  assert.deepEqual(afterRun({ ok: false, code: 'nothing-to-seed' }, c), { next: 'wait', why: 'Not seeding: no project is set to seed.' })
  assert.deepEqual(afterRun({ ok: false, code: 'refused', message: 'the engine command seed is not allowed' }, c),
    { next: 'wait', why: 'Not seeding: the engine command seed is not allowed.' })
  // it ended by itself because of the settings: torrents refused now, or no project set to seed
  assert.deepEqual(afterRun({ ok: true }, { ...c, wanted: false, refused: true }),
    { next: 'wait', why: 'Seeding stopped: torrents are refused in the settings (Settings, Torrents).' })
  assert.deepEqual(afterRun({ ok: true }, { ...c, wanted: false }), { next: 'wait', why: 'Seeding stopped: no project is set to seed now.' })
  assert.deepEqual(afterRun({ ok: false, code: 'no-port', message: 'seeding cannot listen on any port here.' }, { ...c, wanted: false }),
    { next: 'wait', why: 'Seeding stopped: seeding cannot listen on any port here.' })
})

test('a run that ended by itself starts again after a while, a few times at most, said each time', () => {
  const c = { restarting: false, stopped: false, wanted: true, refused: false, ranFor: 1000, retries: 0 }
  const end = { ok: false, code: 'engine-stopped', message: 'the engine stopped without an answer (exit 1)' }
  const seen = []
  let retries = 0
  for (;;) {
    const next = afterRun(end, { ...c, retries })
    seen.push(next)
    if (next.next !== 'retry') break
    retries = next.retries
  }
  assert.deepEqual(seen.slice(0, -1).map((n) => n.after), RETRY)
  assert.equal(seen[0].why, 'Seeding stopped by itself (the engine stopped without an answer (exit 1)): it starts again in 10 seconds.')
  assert.equal(seen[1].why.endsWith('it starts again in a minute.'), true)
  assert.equal(seen[2].why.endsWith('it starts again in 5 minutes.'), true)
  assert.deepEqual(seen.at(-1), { next: 'wait',
    why: 'Seeding stopped by itself (the engine stopped without an answer (exit 1)): it starts again when a seed setting changes, or when DeltaPatcher starts.' })
  // a run that went on for a while counts its ends from none again
  assert.equal(afterRun(end, { ...c, retries: RETRY.length, ranFor: STEADY }).next, 'retry')
  // a run that ended with a result while a project still asks for one: started again too
  assert.equal(afterRun({ ok: true }, c).why, 'Seeding stopped by itself (it ended): it starts again in 10 seconds.')
})

test("the import dialog's words: the mark, what seeding means, what refused torrents leave", () => {
  assert.deepEqual(torrentMark(false), { text: 'By torrent', hint: 'Shared by torrent too: players also download it from each other.' })
  assert.deepEqual(torrentMark(true), { text: 'By torrent only', hint: 'It has no address: players download it from each other only.' })
  assert.equal(SEED_IT,
    'Other players download from you while DeltaPatcher runs.\nOnly files DeltaPatcher has checked are offered; others see your IP address.')
  assert.equal(refusedLine(true), 'Torrents are refused in your settings (Settings, Torrents): it updates from its address only.')
  assert.equal(refusedLine(false),
    'Torrents are refused in your settings (Settings, Torrents): it updates only by torrent, so it cannot update here.')
  for (const text of [SEED_IT, refusedLine(true), refusedLine(false), torrentMark(true).hint, NEEDS_SIGNING]) {
    assert.match(text, /^[\x20-\x7e\n]*$/, 'ASCII')
  }
})

test('the torrent switches: sharing needs signed releases, seeding needs a project shared by torrent', () => {
  assert.deepEqual(torrentSwitches({ sign: false, torrent: false, torrent_id: '' }),
    { torrent: NEEDS_SIGNING, seed: 'Nothing to seed: this project is not shared by torrent.' })
  assert.deepEqual(torrentSwitches({ sign: true, torrent: false, torrent_id: '' }).torrent, '')
  // its id already made (a player's from its project file): no signing needed to turn it on again
  assert.deepEqual(torrentSwitches({ sign: false, torrent: false, torrent_id: 'ab'.repeat(16) }).torrent, '')
  // on now: it can always be turned off; set to seed: seeding can always be turned off
  assert.deepEqual(torrentSwitches({ sign: false, torrent: true, torrent_id: '' }), { torrent: '', seed: '' })
  assert.equal(torrentSwitches({ torrent: false, seed: true }).seed, '')
  assert.equal(hasTorrentId('AB'.repeat(16)), true)
  for (const id of ['', 'ab', 'xy'.repeat(16), undefined]) assert.equal(hasTorrentId(id), false, String(id))
})

test('the seeds, the torrent name and the port, checked as they are typed: the engine refuses the same', () => {
  for (const ok of ['', 'game.example.com:6881', '203.0.113.10:6881, game.example.com:51413', 'a.b:1; [2001:db8::1]:6881 x.y:65535']) {
    assert.equal(seedsProblem(ok), '', ok)
  }
  assert.equal(seedsProblem('game.example.com'), 'game.example.com: give it as host:port.')
  assert.equal(seedsProblem('game.example.com:0'), 'game.example.com:0: the port after the : is 1 to 65535.')
  assert.equal(seedsProblem('game.example.com:65536'), 'game.example.com:65536: the port after the : is 1 to 65535.')
  assert.equal(seedsProblem('game.example.com:06881'), 'game.example.com:06881: the port after the : is 1 to 65535.')
  assert.equal(seedsProblem('bad_host:6881'), 'bad_host:6881: a host name or address before the :.')
  assert.equal(seedsProblem('-a.example.com:6881'), '-a.example.com:6881: a host name or address before the :.')
  assert.equal(seedsProblem(Array.from({ length: MAX_SEEDS + 1 }, (_v, i) => `s${i}.example.com:6881`).join(',')), 'At most 20 seeds.')
  assert.equal(seedsProblem(Array.from({ length: MAX_SEEDS + 1 }, () => 'same.example.com:6881').join(',')), '', 'each once')

  for (const ok of ['', 'release', 'my-game_1.2', 'Mods']) assert.equal(torrentNameProblem(ok), '', ok)
  assert.equal(torrentNameProblem('a'.repeat(61)), 'At most 60 characters.')
  for (const bad of ['my game', '.hidden', 'name.', '_x']) {
    assert.equal(torrentNameProblem(bad), 'Letters, digits and . _ - only, starting with a letter or a digit, with no dot at its end.', bad)
  }
  assert.equal(torrentNameProblem('con.txt'), 'con.txt is a name Windows keeps for a device: choose another.')
  assert.equal(torrentNameProblem('Blobs'), 'Blobs is the name of a file or folder of the store: choose another.')
  assert.equal(torrentNameProblem('release.torrent'), 'release.torrent is the name of a file or folder of the store: choose another.')

  for (const ok of ['0', '6881', '65535']) assert.equal(portProblem(ok), '', ok)
  for (const bad of ['', '65536', '-1', '68 81', 'x']) assert.equal(portProblem(bad), 'A port from 1 to 65535, or 0 for any free port.', bad)
})
