// The two types of project file as the window offers them (src/renderer/src/store/privateFiles.ts): public for
// players, private for the publisher on any computer; what each export can hold, the private file's words, and the
// engine runs that set up releasing from a private file with this computer's own folders and keys.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  APPLY_OLDER, EXPORT_TYPES, exportChosen, exportName, exportProblem, hostKeysLine, inPlaceLine, KEY_BY_HAND,
  keyUnknownLine, leaveIt, madeWords, olderLine, picksFor, playerLine, PRIVATE_EXPORT, PRIVATE_IMPORT, privateExportArgs,
  privateImportArgs, privateImportButton, privateRuns, seedAfterSetUp, setUpNotes
} from '../src/renderer/src/store/privateFiles.ts'

const PART = {
  name: 'Game', version: '1.0', protect: 'config/user.cfg', leave_out: '', sign: true, htaccess: true, share_updates: true,
  show_files: true, signing_public_key: 'ed25519:' + 'A'.repeat(43) + '=', deploy_mode: 'sftp', ssh_host: 'game.example.com',
  ssh_user: 'deploy', ssh_port: 22, remote_root: '/var/www/game', public_url: 'https://game.example.com/', access_log: '',
  torrent: false, torrent_id: '', torrent_name: '', seeds: '', backup_keep_daily: 7, backup_keep_weekly: 4,
  backup_keep_monthly: 0, backup_max_size: ''
}
const KEYS = {
  host: 'game.example.com',
  keys: [{ type: 'ssh-ed25519', fingerprint: 'SHA256:abc' }, { type: 'ecdsa-sha2-nistp256', fingerprint: 'SHA256:def' }],
  state: 'new'
}

function row(change = {}) {
  return {
    title: 'Game', name: 'Game', pick: true, asNew: false, private: PART, host_keys: KEYS, publishSrc: 'D:\\dev\\game',
    signingKey: 'C:\\keys\\signing-key.pem', sshKey: 'C:\\keys\\deltapatcher_ed25519', deployFolder: '', seed: false,
    trustHostKeys: false, ...change
  }
}

test('the type comes first: public for players, private for the publisher, in those words', () => {
  assert.deepEqual(EXPORT_TYPES.map((t) => [t.value, t.label]), [['public', 'Public: for players'], ['private', 'Private: for you, on any computer']])
  // one short sentence a line, in ASCII
  const older = olderLine('2026-11-25T14:02:00Z', '2026-11-25T16:40:00Z')
  for (const text of [PRIVATE_EXPORT, PRIVATE_IMPORT, KEY_BY_HAND, older, keyUnknownLine('Game'), playerLine('Game'),
    APPLY_OLDER, leaveIt('Game'), ...EXPORT_TYPES.map((t) => t.hint)]) {
    assert.match(text, /^[\x20-\x7e\n]*$/)
    for (const line of text.split('\n')) assert.ok(line.length <= 90, line)
  }
  assert.match(PRIVATE_EXPORT, /^It holds your server's address and login name, never a key or a password\.\nKeep it to yourself\.\nBring your signing key separately: a USB stick, not chat or email\.$/)
  assert.equal(exportName(['Game'], 'private'), 'Game (private)')
  assert.equal(exportName(['Game'], 'public'), 'Game')
  assert.equal(exportName(['Game', 'Maps'], 'private'), 'Projects (private)')
})

test('what each type can hold: an address for a public file; signed releases, an address or a torrent for a private one', () => {
  const signed = { address: 'https://game.example.com/', sign: true }
  assert.equal(exportProblem(signed, 'public'), '')
  assert.equal(exportProblem(signed, 'private'), '')
  assert.match(exportProblem({ ...signed, sign: false }, 'private'), /not signed/)
  assert.equal(exportProblem({ ...signed, sign: false }, 'public'), '')                    // public files: as before
  assert.match(exportProblem({ address: '', sign: true }, 'public'), /No http\(s\) address/)
  assert.equal(exportProblem({ address: '', torrent: true, sign: true }, 'private'), '')
  assert.match(exportProblem({ address: '', sign: true }, 'private'), /not shared by torrent/)
  assert.deepEqual(privateExportArgs(['a.ini', 'b.ini'], 'D:\\out\\x (private).deltapatch', true),
    ['project', 'export', 'a.ini', 'b.ini', '--out=D:\\out\\x (private).deltapatch', '--private', '--passphrase-stdin'])
  assert.ok(!privateExportArgs(['a.ini'], 'x.html', false).includes('--passphrase-stdin'))
})

test('switching to Private unticks what it cannot hold, so Export is never held back by a tick that cannot be taken off', () => {
  const projects = [
    { file: 'a.ini', address: 'https://a.example.com/', sign: true },
    { file: 'b.ini', address: 'https://b.example.com/', sign: false },
    { file: 'c.ini', address: '', torrent: true, sign: true }
  ]
  const picks = { 'a.ini': true, 'b.ini': true, 'c.ini': false }
  assert.deepEqual(picksFor('private', projects, picks), { 'a.ini': true, 'b.ini': false, 'c.ini': false })
  assert.deepEqual(exportChosen(projects, picks, 'private').map((p) => p.file), ['a.ini'])
  // only B ticked, which a private file cannot hold: nothing goes, so Export waits for a tick that can be given
  assert.deepEqual(exportChosen(projects, { 'b.ini': true }, 'private'), [])
  assert.deepEqual(exportChosen(projects, { 'b.ini': true }, 'public').map((p) => p.file), ['b.ini'])
  assert.deepEqual(picksFor('public', projects, { 'c.ini': true }), { 'a.ini': false, 'b.ini': false, 'c.ini': false })
})

test("a project added gets this computer's folders and keys; the host keys only when the person says so", () => {
  assert.deepEqual(privateImportArgs('D:\\x.deltapatch', row(), false), [
    'project', 'import', 'D:\\x.deltapatch', '--only=Game', '--yes', '--publish-src=D:\\dev\\game',
    '--signing-key=C:\\keys\\signing-key.pem', '--ssh-key=C:\\keys\\deltapatcher_ed25519'
  ])
  // the host keys it showed, by their fingerprints: a file changed since adds nothing
  assert.ok(privateImportArgs('x', row({ trustHostKeys: true }), false).includes('--host-key-fingerprints=SHA256:abc,SHA256:def'))
  assert.ok(!privateImportArgs('x', row({ trustHostKeys: true }), false).some((a) => a.startsWith('--trust-host-keys')))
  // trusted already, or other keys trusted for that server here: never added from the file
  for (const state of ['known', 'other', 'unreadable', '']) {
    assert.ok(!privateImportArgs('x', row({ trustHostKeys: true, host_keys: { ...KEYS, state } }), false).some((a) => a.startsWith('--host-key')), state)
  }
  assert.ok(!privateImportArgs('x', row({ trustHostKeys: true, host_keys: { ...KEYS, keys: [] } }), false).some((a) => a.startsWith('--host-key')))
  // a folder release: the folder it is copied to, and no SSH key; empty fields are chosen later
  const folder = privateImportArgs('x', row({ private: { ...PART, deploy_mode: 'folder' }, deployFolder: 'E:\\www', publishSrc: ' ' }), false)
  assert.ok(folder.includes('--deploy-folder=E:\\www'))
  assert.ok(!folder.some((a) => a.startsWith('--ssh-key') || a.startsWith('--publish-src')))
  // seeding: only for a project shared by torrent, and not while torrents are refused
  assert.ok(privateImportArgs('x', row({ seed: true, torrent: { id: 'x' } }), false).includes('--seed'))
  assert.ok(!privateImportArgs('x', row({ seed: true, torrent: { id: 'x' } }), true).includes('--seed'))
  assert.ok(!privateImportArgs('x', row({ seed: true }), false).includes('--seed'))
  // another name here
  assert.ok(privateImportArgs('x', row({ name: 'Game 2' }), false).includes('--name=Game 2'))
})

test('a project here it updates keeps its own folders and keys; or it is set up as a new one', () => {
  const here = { project: 'Game', file: 'C:\\settings\\projects\\Game.ini' }
  assert.deepEqual(privateImportArgs('x', row({ updates: here }), false), ['project', 'import', 'x', '--only=Game', '--yes'])
  assert.deepEqual(privateImportArgs('x', row({ updates: here, trustHostKeys: true }), false),
    ['project', 'import', 'x', '--only=Game', '--yes', '--host-key-fingerprints=SHA256:abc,SHA256:def'])
  const asNew = privateImportArgs('x', row({ updates: here, asNew: true }), false)
  assert.ok(asNew.includes('--as-new') && asNew.includes('--publish-src=D:\\dev\\game'))
  assert.equal(privateImportButton([row()]), 'Set up releasing')
  assert.equal(privateImportButton([row({ updates: here })]), 'Update it')
  assert.equal(privateImportButton([row(), row({ title: 'Maps', updates: here })]), 'Set up 1, update 1')
  assert.equal(privateImportButton([row()], true), 'Setting up...')
})

test('the host keys and what is left to do after it is set up, in plain lines', () => {
  assert.match(hostKeysLine(KEYS), /^Trust them only if this file is yours\.\nThen the first release does not ask about the server\.$/)
  assert.match(hostKeysLine({ ...KEYS, state: 'known' }), /trusts them already/)
  assert.match(hostKeysLine({ ...KEYS, state: 'other' }), /^The host keys this computer trusts for game\.example\.com differ from the file's\.\nThe file's are not added\.$/)
  assert.equal(hostKeysLine({ ...KEYS, state: '' }), '')
  assert.equal(hostKeysLine(undefined), '')
  assert.deepEqual(setUpNotes([{ project: 'Game', key_check: 'matches', host_keys: 'added', publish_src: 'D:\\dev\\game' }]), [])
  assert.deepEqual(setUpNotes([{ project: 'Game', key_check: 'first release', host_keys: 'not added', publish_src: '' }]), [
    'Game: choose the folder to release on its Release page.',
    'Game: the first release checks your signing key, when it asks its passphrase.',
    'Game: the first release asks about the server.'
  ])
  assert.equal(playerLine('Game'), 'You have Game as a player.\nThis file sets up releasing it, as a project of its own.')
})

test("a seed run starts from the engine's own answer for each project set up, never from a name", () => {
  assert.equal(seedAfterSetUp([{ seed: false }, { seed: true }]), true)
  assert.equal(seedAfterSetUp([{ seed: false }, {}]), false)
  assert.equal(seedAfterSetUp([]), false)
})

test('a file older than the one applied here: both times, left as it is unless the person says Apply it anyway', () => {
  const here = { project: 'Game', file: 'C:\\settings\\projects\\Game.ini' }
  const changes = [{ key: 'description', label: 'description', from: 'The newer one', to: 'The older one' }]
  const old = row({ updates: here, older: true, made: '2026-11-25T14:02:00Z', applied_made: '2026-11-25T16:40:00Z', changes })
  assert.equal(madeWords('2026-11-25T14:02:00Z'), '2026-11-25 14:02 UTC')
  assert.equal(inPlaceLine('Game', old), 'This file was made 2026-11-25 14:02 UTC.\nThe one applied here was made 2026-11-25 16:40 UTC.\n' +
    'Opening it puts back what it says.\nGame stays as it is, unless you apply it anyway.')
  assert.equal(APPLY_OLDER, 'Apply it anyway')
  // not applied by default: no engine run for it, and nothing for the button to do
  assert.deepEqual(privateRuns([old]), [])
  assert.deepEqual(privateRuns([old, row({ title: 'Maps' })]).map((e) => e.title), ['Maps'])
  assert.equal(privateImportButton([old, row({ title: 'Maps' })]), 'Set up releasing')
  // Apply it anyway: run, with --apply-older (and only then)
  const anyway = { ...old, applyOlder: true }
  assert.deepEqual(privateRuns([anyway]).map((e) => e.title), ['Game'])
  assert.deepEqual(privateImportArgs('x', anyway, false), ['project', 'import', 'x', '--only=Game', '--yes', '--apply-older'])
  assert.ok(!privateImportArgs('x', row({ updates: here, changes }), false).includes('--apply-older'))
  assert.equal(privateImportButton([anyway]), 'Update it')
  assert.match(inPlaceLine('Game', anyway), /\nThis updates Game anyway:$/)
  assert.equal(leaveIt('Game'), 'Leave Game as it is')
  // a newer file: as before
  assert.equal(inPlaceLine('Game', row({ updates: here, changes })), 'This updates Game:')
  assert.equal(inPlaceLine('Game', row({ updates: here })), 'This updates Game.\nNothing in it changes.')
})

test('a project here of its name released with a key DeltaPatcher does not know yet is named, and why', () => {
  assert.equal(keyUnknownLine('Game'), 'Game releases here too, but DeltaPatcher does not know its signing key yet.\n' +
    'Release it once or export a private file from it, then open this file again.\nOr set this one up as a separate project.')
})
