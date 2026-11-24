// Starting with the computer, straight into the tray (the setting start_with_system). Windows: a login item
// that runs the program with HIDDEN_ARG. macOS: a login item; it cannot pass an argument, so the app asks
// whether it was opened at login. Linux: an autostart .desktop file that runs the AppImage (or the program)
// with HIDDEN_ARG. Off removes only this copy's own registration, never another copy's, and a copy run from
// source never registers or removes anything. Everything outside this file comes in through System, so a
// test can stand in for the system.
import { posix } from 'path'

// the argument that starts DeltaPatcher hidden in the tray
export const HIDDEN_ARG = '--hidden'
// Windows: the name of the login item
export const LOGIN_ITEM_NAME = 'DeltaPatcher'
// the line that marks an autostart file as this switch's own: no other file is ever removed
const MARK = 'X-DeltaPatcher-Start-With-System=true'

// what Electron's app.setLoginItemSettings takes, the parts used here
export interface LoginItem {
  openAtLogin: boolean
  openAsHidden?: boolean
  path?: string
  args?: string[]
  name?: string
}

export interface System {
  platform: string
  packaged: boolean
  execPath: string
  env: Record<string, string | undefined>
  home: string
  setLoginItem: (item: LoginItem) => void
  // whether this copy is the login item now (Windows: the login item LOGIN_ITEM_NAME runs this program)
  isLoginItem: () => boolean
  // null when there is no such file
  readFile: (path: string) => string | null
  // makes the file's folder first
  writeFile: (path: string, text: string) => void
  removeFile: (path: string) => void
}

// '' when this copy can start with the computer; else why not, one sentence per line
export function unavailable(sys: System): string {
  if (!sys.packaged) return 'This copy runs from source.\nOnly an installed DeltaPatcher can start with the computer.'
  if (!['win32', 'darwin', 'linux'].includes(sys.platform)) return 'DeltaPatcher cannot start with this system.'
  return ''
}

// Linux: the autostart file, in $XDG_CONFIG_HOME/autostart or else ~/.config/autostart
export function autostartFile(sys: System): string {
  const xdg = sys.env.XDG_CONFIG_HOME
  const base = xdg && posix.isAbsolute(xdg) ? xdg : posix.join(sys.home, '.config')
  return posix.join(base, 'autostart', 'deltapatcher.desktop')
}

// a value of a desktop entry: a backslash is written twice
function entryValue(text: string): string {
  return text.replace(/\\/g, '\\\\')
}

// one argument of an Exec line, quoted as the desktop entry specification asks: a backslash before each
// " ` $ and \ inside the quotes, then written as a value, and % written twice
function execArg(text: string): string {
  const quoted = text.replace(/[\\"`$]/g, (c) => '\\' + c)
  return '"' + entryValue(quoted).replace(/%/g, '%%') + '"'
}

// the autostart file's text: it runs `program` hidden in the tray, and is skipped once the program is gone
export function desktopEntry(program: string): string {
  if (/[\x00-\x1f\x7f]/.test(program)) throw new Error('the program\'s path cannot go in an autostart file')
  return [
    '[Desktop Entry]',
    'Type=Application',
    `Name=${LOGIN_ITEM_NAME}`,
    'Comment=DeltaPatcher, started with the computer, in the tray',
    `TryExec=${entryValue(program)}`,
    `Exec=${execArg(program)} ${HIDDEN_ARG}`,
    'Terminal=false',
    'X-GNOME-Autostart-enabled=true',
    MARK,
    ''
  ].join('\n')
}

// The system put in step with the switch: on, DeltaPatcher starts at login, hidden in the tray; off, this
// copy's registration is removed. '' when done, else what went wrong.
export function apply(sys: System, on: boolean): string {
  if (unavailable(sys)) return ''
  try {
    if (sys.platform === 'win32') {
      // on at every start, so the path and the argument stay right; off only when this copy is the login item
      const item = { openAtLogin: on, path: sys.execPath, args: [HIDDEN_ARG], name: LOGIN_ITEM_NAME }
      if (on || sys.isLoginItem()) sys.setLoginItem(item)
    } else if (sys.platform === 'darwin') {
      if (sys.isLoginItem() !== on) sys.setLoginItem({ openAtLogin: on, openAsHidden: on })
    } else {
      const file = autostartFile(sys)
      const now = sys.readFile(file)
      const program = sys.env.APPIMAGE || sys.execPath
      const lines = now === null ? [] : now.split(/\r?\n/)
      if (on) {
        const text = desktopEntry(program)
        if (now !== text) sys.writeFile(file, text)
      } else if (lines.includes(MARK) && lines.includes(`TryExec=${entryValue(program)}`)) {
        sys.removeFile(file)
      }
    }
    return ''
  } catch (err) {
    return err instanceof Error ? err.message : String(err)
  }
}

// whether this start came from the login item: the window then stays in the tray until it is asked for
export function startedHidden(platform: string, argv: string[], openedAtLogin: boolean): boolean {
  return argv.includes(HIDDEN_ARG) || (platform === 'darwin' && openedAtLogin)
}
