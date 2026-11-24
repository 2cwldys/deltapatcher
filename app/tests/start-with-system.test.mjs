// Starting with the computer (src/main/startWithSystem.ts), against a system that only records what is asked
// of it: login items on Windows and macOS, the autostart file on Linux, nothing from source.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  apply,
  autostartFile,
  desktopEntry,
  HIDDEN_ARG,
  startedHidden,
  unavailable
} from '../src/main/startWithSystem.ts'

function fake(over = {}) {
  const calls = []
  const files = new Map()
  let loginItem = false
  const sys = {
    platform: 'win32',
    packaged: true,
    execPath: 'C:\\Users\\player\\AppData\\Local\\Programs\\DeltaPatcher\\DeltaPatcher.exe',
    env: {},
    home: '/home/player',
    setLoginItem: (item) => {
      calls.push(item)
      loginItem = item.openAtLogin
    },
    isLoginItem: () => loginItem,
    readFile: (path) => (files.has(path) ? files.get(path) : null),
    writeFile: (path, text) => files.set(path, text),
    removeFile: (path) => files.delete(path),
    ...over
  }
  return { sys, calls, files }
}

test('Windows: a login item that starts it hidden, and off removes it', () => {
  const { sys, calls } = fake()
  assert.equal(apply(sys, false), '')
  assert.equal(calls.length, 0)                         // off, and not this copy's login item: nothing to do
  assert.equal(apply(sys, true), '')
  assert.deepEqual(calls, [{ openAtLogin: true, path: sys.execPath, args: [HIDDEN_ARG], name: 'DeltaPatcher' }])
  assert.equal(apply(sys, true), '')                    // on at every start: the path and the argument kept right
  assert.equal(calls.length, 2)
  assert.equal(apply(sys, false), '')
  assert.deepEqual(calls[2], { openAtLogin: false, path: sys.execPath, args: [HIDDEN_ARG], name: 'DeltaPatcher' })
})

test("Windows: another copy's login item is left alone", () => {
  const { sys, calls } = fake({ isLoginItem: () => false })
  assert.equal(apply(sys, false), '')
  assert.equal(calls.length, 0)
})

test('macOS: a login item, changed only when it is not as the switch says', () => {
  const { sys, calls } = fake({ platform: 'darwin', execPath: '/Applications/DeltaPatcher.app/Contents/MacOS/DeltaPatcher' })
  assert.equal(apply(sys, false), '')
  assert.equal(calls.length, 0)                         // off, and it is not one: nothing to do
  assert.equal(apply(sys, true), '')
  assert.equal(apply(sys, true), '')
  assert.deepEqual(calls, [{ openAtLogin: true, openAsHidden: true }])
  assert.equal(apply(sys, false), '')
  assert.deepEqual(calls[1], { openAtLogin: false, openAsHidden: false })
})

test('Linux: an autostart file that runs the AppImage hidden; off removes only that file', () => {
  const { sys, files } = fake({ platform: 'linux', execPath: '/tmp/.mount_Delta/deltapatcher',
    env: { APPIMAGE: '/home/player/Apps/DeltaPatcher-1.0.AppImage' } })
  const file = '/home/player/.config/autostart/deltapatcher.desktop'
  assert.equal(autostartFile(sys), file)
  assert.equal(apply(sys, true), '')
  const text = files.get(file)
  assert.match(text, /^\[Desktop Entry\]\n/)
  assert.ok(text.includes('\nExec="/home/player/Apps/DeltaPatcher-1.0.AppImage" --hidden\n'), text)
  assert.ok(text.includes('\nTryExec=/home/player/Apps/DeltaPatcher-1.0.AppImage\n'), text)
  assert.equal(apply(sys, false), '')
  assert.equal(files.has(file), false)
  files.set(file, '[Desktop Entry]\nExec=deltapatcher\n')     // made by someone else: left alone
  assert.equal(apply(sys, false), '')
  assert.equal(files.get(file), '[Desktop Entry]\nExec=deltapatcher\n')
  const other = desktopEntry('/home/player/Old/DeltaPatcher-0.9.AppImage')
  files.set(file, other)                                      // another copy's: left alone too
  assert.equal(apply(sys, false), '')
  assert.equal(files.get(file), other)
})

test('Linux: XDG_CONFIG_HOME, the program itself without an AppImage, and a failure said', () => {
  const { sys, files } = fake({ platform: 'linux', execPath: '/opt/DeltaPatcher/deltapatcher', env: { XDG_CONFIG_HOME: '/srv/cfg' } })
  assert.equal(apply(sys, true), '')
  assert.ok(files.get('/srv/cfg/autostart/deltapatcher.desktop').includes('\nExec="/opt/DeltaPatcher/deltapatcher" --hidden\n'))
  const broken = fake({ platform: 'linux', writeFile: () => { throw new Error('read-only file system') } })
  assert.equal(apply(broken.sys, true), 'read-only file system')
})

test('a path with spaces, quotes, dollars, backslashes and percent signs is quoted for the Exec line', () => {
  const text = desktopEntry('/home/a b/"x"$y\\z%1.AppImage')
  assert.ok(text.includes('\nExec="/home/a b/\\\\"x\\\\"\\\\$y\\\\\\\\z%%1.AppImage" --hidden\n'), text)
  assert.throws(() => desktopEntry('/home/a\nb'))
})

test('from source nothing is registered or removed, and the switch says why', () => {
  const { sys, calls, files } = fake({ packaged: false })
  assert.match(unavailable(sys), /runs from source/)
  assert.equal(apply(sys, true), '')
  assert.equal(apply(sys, false), '')
  assert.equal(calls.length, 0)
  const linux = fake({ platform: 'linux', packaged: false })
  linux.files.set('/home/player/.config/autostart/deltapatcher.desktop', 'an installed copy\'s')
  apply(linux.sys, false)
  assert.equal(linux.files.size, 1)
  assert.equal(files.size, 0)
  assert.equal(unavailable(fake().sys), '')
})

test('started hidden: the argument, or macOS opened at login', () => {
  assert.equal(startedHidden('win32', ['DeltaPatcher.exe', HIDDEN_ARG], false), true)
  assert.equal(startedHidden('win32', ['DeltaPatcher.exe'], true), false)
  assert.equal(startedHidden('darwin', ['DeltaPatcher'], true), true)
  assert.equal(startedHidden('linux', ['deltapatcher', 'Mod.deltapatch'], false), false)
})
