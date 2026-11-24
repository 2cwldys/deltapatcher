// A project's publisher as the window shows it (src/renderer/src/store/publisher.ts): the prompt (Apply, Not now)
// and what answering it leaves, the "What changed" lists with show_files on and off, the import dialog's words for
// update capable files, the export dialog's choice, and the release notes box.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  acceptArgs, applyQuestion, changeLists, fileList, hasWhatChanged, importButton, inPlace, nameLines, notesProblem,
  NOTES_MAX, promptLine, promptOf, settled, UPDATE_CAPABLE, updateLine, updatesDefault, withoutPrompt
} from '../src/renderer/src/store/publisher.ts'

const DETAILS = {
  changes: [
    { field: 'description', label: 'description', from: 'Twelve maps', to: 'Twelve arena maps' },
    { field: 'website', label: 'website', from: '', to: 'https://maps.example.com/' }
  ],
  words: 'description, website',
  hash: 'a'.repeat(64),
  moves: [],
  trust: '',
  warning: '',
  signer: '',
  trusted: '',
  applicable: ['description', 'website']
}

const MOVE = {
  ...DETAILS,
  changes: [{ field: 'source_url', label: 'the address', from: 'https://old.example.com/', to: 'https://new.example.com/' }],
  words: 'the address',
  hash: 'b'.repeat(64),
  moves: ['source_url'],
  trust: 'unsigned',
  warning: 'It moves the project to another address: it updates from there from now on.\nThis project trusts no publisher key: anyone who can change that server could have sent this.',
  applicable: ['source_url']
}

const CHECKED = {
  empty: false, add: 2, replace: 1, remove: 1, same: 4, download_bytes: 100, download_blobs: 3, publisher_key: '', kept: [],
  show_files: true,
  changes: { new: ['maps/b.map', 'maps/c.map'], changed: ['readme.txt'], removed: ['maps/a.map'], kept: ['config/user.cfg'] },
  files: [{ path: 'maps/b.map', size: 10 }, { path: 'maps/c.map', size: 20 }, { path: 'readme.txt', size: 5 }],
  notes: 'Two new maps.',
  publisher_change: DETAILS
}

test('the prompt: one line, shown while nothing runs on the project', () => {
  assert.equal(promptLine(DETAILS), 'The publisher changed this project: description, website.')
  assert.equal(promptOf({ state: 'done', result: CHECKED }), DETAILS)
  for (const state of ['waiting', 'checking', 'updating', 'failed']) assert.equal(promptOf({ state, result: CHECKED }), undefined, state)
  assert.equal(promptOf(undefined), undefined)
  assert.equal(promptOf({ state: 'done', result: withoutPrompt(CHECKED) }), undefined)
})

test('Apply asks first, with the warning a new address comes with, and applies only the change shown', () => {
  const q = applyQuestion('Map Pack', DETAILS)
  assert.equal(q.title, "Apply the publisher's change to Map Pack?")
  assert.match(q.body, /^description: Twelve maps -> Twelve arena maps\nwebsite: \(none\) -> https:\/\/maps.example.com\//)
  assert.match(q.body, /Its folder, its name here and your own settings stay as they are\.$/)
  assert.equal(q.danger, false)
  const moving = applyQuestion('Map Pack', MOVE)
  assert.equal(moving.danger, true, 'a move nobody signed is asked in red')
  assert.ok(moving.body.includes('anyone who can change that server could have sent this'))
  assert.equal(applyQuestion('Map Pack', { ...MOVE, trust: 'signed' }).danger, false)
  const refused = applyQuestion('Map Pack', { ...MOVE, trust: 'refused', changes: [...MOVE.changes, DETAILS.changes[0]], applicable: ['description'] })
  assert.ok(refused.body.includes('Not applied: the address.'))
  assert.deepEqual(acceptArgs('D:\\p\\Map Pack.ini', DETAILS, false), ['project', 'accept', 'D:\\p\\Map Pack.ini', '--yes', `--expect=${'a'.repeat(64)}`])
  assert.deepEqual(acceptArgs('Map Pack.ini', MOVE, true), ['project', 'accept', 'Map Pack.ini', '--not-now', `--expect=${'b'.repeat(64)}`])
})

test('answered, the prompt leaves the row and the Update page of that project, and nothing else', () => {
  const runs = { a: { state: 'done', check: true, result: CHECKED }, b: { state: 'done', check: true, result: CHECKED } }
  const results = { update: { ...CHECKED, check: true }, release: { x: 1 } }
  const after = settled(runs, results, 'a', 'a')
  assert.equal(after.runs.a.result.publisher_change, undefined)
  assert.equal(after.runs.a.result.notes, 'Two new maps.', 'the rest of the result stays')
  assert.equal(after.runs.b.result.publisher_change, DETAILS, 'another project keeps its own')
  assert.equal(after.results.update.publisher_change, undefined)
  assert.equal(after.results.update.check, true)
  assert.equal(after.results.release, results.release)
  const elsewhere = settled(runs, results, 'b', 'a')
  assert.equal(elsewhere.results.update.publisher_change, DETAILS, "the page shows another project's result: kept")
  assert.equal(settled({}, {}, 'a', 'a').runs.a, undefined)
  assert.equal(runs.a.result.publisher_change, DETAILS, 'nothing changed in place')
})

test('show_files on: the files by name, searched; off: counts only', () => {
  assert.equal(hasWhatChanged(CHECKED), true)
  const lists = changeLists(CHECKED)
  assert.deepEqual(lists.map((l) => [l.label, l.total]), [['New', 2], ['Changed', 1], ['Removed', 1], ['Kept: your own copies', 1]])
  const found = changeLists(CHECKED, 'MAPS c')
  assert.deepEqual(found.map((l) => l.names), [['maps/c.map'], [], [], []], 'every word, letter case aside')
  assert.deepEqual(changeLists(CHECKED, '', 1)[0], { key: 'new', label: 'New', names: ['maps/b.map'], matched: 2, total: 2 })
  assert.deepEqual(fileList(CHECKED, 'readme'), { files: [{ path: 'readme.txt', size: 5 }], matched: 1, total: 3 })
  assert.deepEqual(nameLines(CHECKED, 1), ['New: maps/b.map and 1 more', 'Changed: readme.txt', 'Removed: maps/a.map',
    'Kept: your own copies: config/user.cfg'])
  const off = { ...CHECKED, show_files: false, changes: undefined, files: undefined, notes: undefined }
  assert.deepEqual(changeLists(off), [])
  assert.deepEqual(nameLines(off), [])
  assert.equal(fileList(off), null)
  assert.equal(hasWhatChanged(off), false)
  assert.equal(hasWhatChanged({ ...off, notes: 'Only notes.' }), true, 'the notes are the publisher\'s, shown either way')
  assert.equal(hasWhatChanged(undefined), false)
})

test('the import dialog: update capable, what a file updates, and its button', () => {
  assert.equal(UPDATE_CAPABLE, 'Update capable: the publisher can change this project later; you are asked before anything changes.')
  const updates = { project: 'Map Pack', file: 'Map Pack.ini' }
  const upd = { title: 'Map Pack', pick: true, updates, change: DETAILS, asNew: false }
  const fresh = { title: 'Sounds', pick: true, asNew: false }
  assert.equal(inPlace(upd), true)
  assert.equal(inPlace({ ...upd, asNew: true }), false, 'Add it as a new project instead')
  assert.equal(inPlace(fresh), false)
  assert.equal(updateLine(upd), 'This updates Map Pack: description, website.')
  assert.equal(updateLine({ ...upd, change: null }), 'This updates Map Pack.\nNothing in it changes.')
  assert.equal(updateLine({ ...upd, change: null, older: true }), 'This updates Map Pack.\nIt has details newer than this file, so nothing changes.')
  assert.equal(importButton([fresh]), 'Add 1 project')
  assert.equal(importButton([fresh, { ...fresh, title: 'More' }]), 'Add 2 projects')
  assert.equal(importButton([upd]), 'Update 1 project')
  assert.equal(importButton([upd, fresh]), 'Add 1, update 1')
  assert.equal(importButton([{ ...upd, asNew: true }, fresh]), 'Add 2 projects')
  assert.equal(importButton([{ ...upd, pick: false }, fresh]), 'Add 1 project')
  assert.equal(importButton([upd], true), 'Adding...')
})

test('the export dialog starts update capable when every project shares its updates', () => {
  assert.equal(updatesDefault([{ share_updates: true }]), true)
  assert.equal(updatesDefault([{}]), true, 'the default of share_updates')
  assert.equal(updatesDefault([{ share_updates: true }, { share_updates: false }]), false)
  assert.equal(updatesDefault([]), false)
})

test('release notes: up to 4000 characters', () => {
  assert.equal(NOTES_MAX, 4000)
  assert.equal(notesProblem(''), '')
  assert.equal(notesProblem('x'.repeat(4000)), '')
  assert.equal(notesProblem('x'.repeat(4001)), 'At most 4000 characters: this is 4001.')
})
