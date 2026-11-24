// The folder a project of a project file goes into (src/renderer/src/store/folders.ts): what the import
// dialog suggests when a folder is picked, and the name rules the Settings page says while it is typed.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { folderNameProblem, nameRules, suggestedFolder } from '../src/renderer/src/store/folders.ts'

test('a folder picked gets the folder the file names inside it', () => {
  assert.equal(suggestedFolder('D:\\Games\\The Game', 'Mods'), 'D:\\Games\\The Game\\Mods')
  assert.equal(suggestedFolder('D:\\Games\\The Game\\', 'Mods'), 'D:\\Games\\The Game\\Mods')
  assert.equal(suggestedFolder('C:\\', 'Mods'), 'C:\\Mods')
  assert.equal(suggestedFolder('/home/player/games/the-game', 'Mods'), '/home/player/games/the-game/Mods')
  assert.equal(suggestedFolder('/', 'Mods'), '/Mods')
})

test('a folder that already is that one stays as it is', () => {
  assert.equal(suggestedFolder('D:\\Games\\The Game\\Mods', 'Mods'), 'D:\\Games\\The Game\\Mods')
  assert.equal(suggestedFolder('D:\\Games\\The Game\\mods\\', 'Mods'), 'D:\\Games\\The Game\\mods')     // Windows: case aside
  assert.equal(suggestedFolder('/home/player/game/Mods', 'Mods'), '/home/player/game/Mods')
  assert.equal(suggestedFolder('/home/player/game/mods', 'Mods'), '/home/player/game/mods/Mods')
})

test('no folder named by the file: the folder picked', () => {
  assert.equal(suggestedFolder('D:\\Games\\The Game', ''), 'D:\\Games\\The Game')
})

test("the folder's name follows a project's name rules", () => {
  for (const ok of ['Mods', 'Mods (2)', 'my_mods-1.x', '', '  Mods  ']) assert.equal(folderNameProblem(ok), '', ok)
  for (const bad of ['a/b', 'a\\b', '..', '.', 'CON', 'lpt1.txt', '-x', 'x'.repeat(61), 'a:b', '@x']) {
    assert.notEqual(folderNameProblem(bad), '', bad)
  }
  assert.equal(nameRules('Map Pack'), '')
  assert.equal(nameRules('what?'), 'Letters, digits, spaces and - _ . ( ) only.')
})
