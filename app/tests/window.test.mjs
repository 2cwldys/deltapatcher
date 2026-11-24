// The window's own state and words (src/renderer/src/store): which cards are folded and how that is remembered,
// the welcome screen, the Settings page's plain words and its More, and the line the server setup shows.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { foldStore, isFolded, readFolds, withFold } from '../src/renderer/src/store/folds.ts'
import { closeWelcome, isSetUp, welcomeAtStart, welcomeClosed } from '../src/renderer/src/store/welcome.ts'
import { HIDDEN, splitMore, WORDS, wordsOf } from '../src/renderer/src/store/settingWords.ts'
import { serverSetup, signingSetup } from '../src/renderer/src/store/setup.ts'

// a window storage in memory; `broken` throws as a storage that cannot be used does
function memory(start = {}, broken = false) {
  const kept = { ...start }
  return {
    kept,
    getItem: (k) => {
      if (broken) throw new Error('no storage')
      return k in kept ? kept[k] : null
    },
    setItem: (k, v) => {
      if (broken) throw new Error('no storage')
      kept[k] = String(v)
    }
  }
}

test('every card is open until it is folded; one can start folded; what was done by hand wins', () => {
  assert.equal(isFolded({}, 'release/stats'), false)
  assert.equal(isFolded({}, 'release/setup', true), true)
  assert.equal(isFolded({ 'release/setup': false }, 'release/setup', true), false)
  assert.equal(isFolded({ 'release/stats': true }, 'release/stats'), true)
  assert.equal(isFolded({ 'release/stats': true }, 'release/what'), false)
  // a card's id is never confused with what every object has
  assert.equal(isFolded({}, 'constructor'), false)
  assert.deepEqual(withFold({ a: true }, 'b', false), { a: true, b: false })
})

test('the folds are remembered in the window storage, per page and card', () => {
  const storage = memory()
  const store = foldStore(storage)
  store.getState().setFolded('release/stats', true)
  store.getState().setFolded('settings/backup', true)
  store.getState().setFolded('settings/backup', false)
  assert.deepEqual(store.getState().folds, { 'release/stats': true, 'settings/backup': false })
  // the next start reads them back
  assert.deepEqual(foldStore(storage).getState().folds, { 'release/stats': true, 'settings/backup': false })
  assert.deepEqual(readFolds(storage), { 'release/stats': true, 'settings/backup': false })
})

test('a storage that is empty, damaged or not there opens every card', () => {
  assert.deepEqual(readFolds(null), {})
  assert.deepEqual(readFolds(memory()), {})
  assert.deepEqual(readFolds(memory({ 'deltapatcher.folded': 'not json' })), {})
  assert.deepEqual(readFolds(memory({ 'deltapatcher.folded': '[true]' })), {})
  assert.deepEqual(readFolds(memory({ 'deltapatcher.folded': '{"a": true, "b": "yes", "c": 1, "d": false}' })), { a: true, d: false })
  assert.deepEqual(readFolds(memory({}, true)), {})
  // a storage that cannot keep it: the card still folds now
  const store = foldStore(memory({}, true))
  store.getState().setFolded('release/stats', true)
  assert.equal(isFolded(store.getState().folds, 'release/stats'), true)
})

test('the welcome screen: at the start while nothing is set up, until it is closed', () => {
  const none = [{ install_dir: '', publish_src: '' }, { install_dir: '', publish_src: '', missing: true }]
  assert.equal(isSetUp({ install_dir: 'D:\\Game', publish_src: '' }), true)
  assert.equal(isSetUp({ install_dir: '', publish_src: 'D:\\Release' }), true)
  assert.equal(isSetUp({ install_dir: 'D:\\Game', publish_src: '', missing: true }), false)
  const storage = memory()
  assert.equal(welcomeAtStart(none, storage), true)
  assert.equal(welcomeAtStart([...none, { install_dir: 'D:\\Game', publish_src: '' }], storage), false)
  closeWelcome(storage)
  assert.equal(welcomeClosed(storage), true)
  assert.equal(welcomeAtStart(none, storage), false)
  // no storage: it shows, and closing it does not fail
  assert.equal(welcomeAtStart(none, null), true)
  assert.equal(welcomeAtStart(none, memory({}, true)), true)
  closeWelcome(memory({}, true))
  closeWelcome(null)
})

// the engine's settings, from deltapatcher/config.py: [section, key]
function schema() {
  const text = readFileSync(new URL('../../deltapatcher/config.py', import.meta.url), 'utf8')
  return [...text.matchAll(/^\s*\("([a-z]+)", "([a-z_]+)",/gm)].map((m) => [m[1], m[2]])
}

test('the Settings page leaves out only what DeltaPatcher or the Projects page keeps', () => {
  assert.deepEqual(HIDDEN, ['title', 'project_files', 'current_project', 'publisher_accepted', 'publisher_not_now'])
  const keys = schema().map(([, k]) => k)
  for (const key of HIDDEN) assert.ok(keys.includes(key), `${key} is a setting`)
})

test("every setting the Settings page shows has plain words: a label, one short line, in ASCII", () => {
  const keys = schema().filter(([, k]) => !HIDDEN.includes(k))
  assert.ok(keys.length > 30, 'the schema was read')
  for (const [, key] of keys) {
    const w = WORDS[key]
    assert.ok(w, `${key} has words`)
    assert.ok(w.label && w.label !== key, `${key} has a label in words`)
    assert.ok(w.line && w.line.length <= 70 && !w.line.includes('\n'), `${key}: one short line`)
    assert.match(w.label + w.line, /^[\x20-\x7e]*$/, `${key}: ASCII`)
  }
  // within a section no two settings share a label
  for (const section of new Set(keys.map(([s]) => s))) {
    const labels = keys.filter(([s]) => s === section).map(([, k]) => WORDS[k].label)
    assert.equal(new Set(labels).size, labels.length, `${section}: labels differ`)
  }
  // a setting the window does not know yet keeps its own name
  assert.deepEqual(wordsOf('something_new'), { label: 'something_new', line: '' })
})

test('More holds the settings rarely changed; each part keeps the order of the settings file', () => {
  const keys = schema()
  for (const section of new Set(keys.map(([s]) => s))) {
    const own = keys.filter(([s, k]) => s === section && !HIDDEN.includes(k)).map(([, k]) => k)
    const { shown, more } = splitMore(own)
    assert.deepEqual([...shown, ...more].sort(), [...own].sort(), `${section}: nothing lost`)
    assert.deepEqual(shown, own.filter((k) => shown.includes(k)), `${section}: shown in order`)
    assert.deepEqual(more, own.filter((k) => more.includes(k)), `${section}: More in order`)
    assert.ok(shown.length > 0, `${section}: something shown`)
    assert.ok(more.length > 0, `${section}: something under More`)
  }
  assert.deepEqual(splitMore(['ssh_host', 'ssh_port', 'ssh_user', 'ssh_key']), { shown: ['ssh_host', 'ssh_user'], more: ['ssh_port', 'ssh_key'] })
})

const SSH = { key: 'k', key_exists: true, has_passphrase: true, permission_problems: [], agent: 'keys', in_agent: true, host: 'game.example.com', host_trusted: true }
const KEY = { path: 's', exists: true, has_passphrase: true, can_sign: true }

test('the server setup is ready once the key is there, unlocked and trusted by a trusted host', () => {
  assert.deepEqual(serverSetup(SSH, true, KEY), { ready: true, line: 'Ready: key unlocked, host trusted, releases signed' })
  assert.deepEqual(serverSetup({ ...SSH, has_passphrase: false, in_agent: false }, false, null),
    { ready: true, line: 'Ready: key without a passphrase, host trusted, releases not signed' })
  const not = (ssh, sign = false, key = null) => serverSetup(ssh, sign, key)
  assert.equal(not(null).ready, false)
  assert.match(not(null).line, /^Reading/)
  assert.equal(not({ ...SSH, key_exists: false }).line, 'Not ready: no SSH key yet (New key)')
  assert.equal(not({ ...SSH, permission_problems: ['Everyone can read it'] }).line, 'Not ready: the key is readable by others (Fix permissions)')
  assert.equal(not({ ...SSH, in_agent: false }).line, 'Not ready: the key is locked (Unlock key)')
  assert.equal(not({ ...SSH, host: undefined, host_trusted: undefined }).line, 'Not ready: no host yet (Where players get it)')
  assert.equal(not({ ...SSH, host_trusted: false }).line, 'Not ready: the host key is not confirmed yet (Check host key)')
  assert.equal(not({ ...SSH, host_error: "not a host name or address: 'a b'" }).line, "Not ready: not a host name or address: 'a b'")
  assert.equal(not(SSH, true, { ...KEY, exists: false }).ready, false)
  assert.equal(not(SSH, true, null).ready, false)
})

test('signing alone is done once releases are signed with a key that is there', () => {
  assert.deepEqual(signingSetup(true, KEY), { ready: true, line: 'Releases signed with your key, passphrase protected' })
  assert.deepEqual(signingSetup(true, { ...KEY, has_passphrase: false }), { ready: true, line: 'Releases signed with your key' })
  assert.equal(signingSetup(false, KEY).ready, false)
  assert.equal(signingSetup(true, { ...KEY, exists: false }).ready, false)
  assert.equal(signingSetup(true, null).ready, false)
})
