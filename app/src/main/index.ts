// The DeltaPatcher app: one window, a tray icon, and the engine behind both.
import {
  app,
  BrowserWindow,
  IpcMainEvent,
  IpcMainInvokeEvent,
  Menu,
  Notification,
  Tray,
  clipboard,
  dialog,
  ipcMain,
  nativeImage,
  session,
  shell
} from 'electron'
import { spawn } from 'child_process'
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'fs'
import { homedir, tmpdir } from 'os'
import { basename, dirname, join, resolve } from 'path'
import { InstallKind, RunRequest } from '../shared/types'
import { cancelJob, engineCommand, runEngine, stopAll, stopJob } from './engine'
import { apply as applyStartWithSystem, HIDDEN_ARG, LOGIN_ITEM_NAME, startedHidden, System, unavailable } from './startWithSystem'

// How this copy of DeltaPatcher was installed, which decides how it updates itself: the Windows setup
// (its uninstaller sits beside the program), the Windows portable .exe (it says so in
// PORTABLE_EXECUTABLE_FILE), the Windows zip, an AppImage, macOS, or from source.
function installKind(): InstallKind {
  if (!app.isPackaged) return 'none'
  if (process.platform === 'win32') {
    if (process.env.PORTABLE_EXECUTABLE_FILE) return 'portable'
    return existsSync(join(dirname(process.execPath), 'Uninstall DeltaPatcher.exe')) ? 'setup' : 'zip'
  }
  if (process.platform === 'linux') return process.env.APPIMAGE ? 'appimage' : 'none'
  if (process.platform === 'darwin') return 'dmg'
  return 'none'
}

// Put a downloaded update in place. Only a file the engine downloaded into its own update folder, with a
// DeltaPatcher release's name, is ever run: the window cannot make this start anything else.
async function installUpdate(file: string, kind: string): Promise<string> {
  const folder = resolve(join(tmpdir(), 'deltapatcher-update'))
  const full = resolve(String(file))
  const same = (a: string, b: string): boolean => (process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b)
  if (!same(dirname(full), folder) || !existsSync(full)) return 'refused'
  if (kind === 'setup' && installKind() === 'setup' && /^DeltaPatcher-[\d.]+-setup\.exe$/.test(basename(full))) {
    spawn(full, [], { detached: true, stdio: 'ignore' }).unref()
  } else if (kind === 'appimage' && installKind() === 'appimage' && /^DeltaPatcher-[\d.]+\.AppImage$/.test(basename(full))) {
    const target = String(process.env.APPIMAGE)
    copyFileSync(full, target + '.new')
    chmodSync(target + '.new', 0o755)
    renameSync(target + '.new', target)
    spawn(target, [], { detached: true, stdio: 'ignore' }).unref()
  } else if (kind === 'dmg' && installKind() === 'dmg' && /^DeltaPatcher-[\d.]+-\w+\.dmg$/.test(basename(full))) {
    await shell.openPath(full)                // the disk image opens: the app is dragged over the old one
  } else {
    return 'refused'
  }
  quitting = true
  app.quit()
  return 'started'
}

// README screenshots (--capture=DIR): from source, or a packaged build only when DELTAPATCHER_CAPTURE=1
const captureArg = process.argv.find((a) => a.startsWith('--capture='))
const captureAllowed = !app.isPackaged || process.env.DELTAPATCHER_CAPTURE === '1'
const captureDir = captureArg && captureAllowed ? resolve(captureArg.slice('--capture='.length)) : ''
const devUrl = !app.isPackaged ? process.env['ELECTRON_RENDERER_URL'] : undefined
if (captureDir) app.commandLine.appendSwitch('force-device-scale-factor', '1')

let win: BrowserWindow | null = null
let tray: Tray | null = null
let quitting = false
let closeToTray = false
let minimizeToTray = false
// started with the computer: the first window stays in the tray until it is asked for
let hideFirstWindow = false
// start_with_system as this run last put it in place (null: not yet)
let startWithSystem: boolean | null = null

// what starting with the computer works through: this copy and Electron's login items
function system(): System {
  return {
    platform: process.platform,
    packaged: app.isPackaged,
    // the portable .exe runs from a folder of its own that changes each time: the login item names the .exe itself
    execPath: process.env.PORTABLE_EXECUTABLE_FILE || process.execPath,
    env: process.env,
    home: homedir(),
    setLoginItem: (item) => app.setLoginItemSettings(item),
    isLoginItem: () => {
      const now = app.getLoginItemSettings({ path: process.env.PORTABLE_EXECUTABLE_FILE || process.execPath, args: [HIDDEN_ARG] })
      return process.platform === 'win32' ? (now.launchItems ?? []).some((i) => i.name === LOGIN_ITEM_NAME) : now.openAtLogin
    },
    readFile: (path) => {
      try {
        return readFileSync(path, 'utf8')
      } catch {
        return null
      }
    },
    writeFile: (path, text) => {
      mkdirSync(dirname(path), { recursive: true })
      writeFileSync(path, text, { mode: 0o644 })
    },
    removeFile: (path) => rmSync(path, { force: true })
  }
}

// A project file (.deltapatch, .html) or a settings file (.ini) DeltaPatcher was asked to open: double-clicked,
// given on the command line, or opened from Finder. The window takes it once it is ready; one that comes while
// it runs is handed over at once.
const OPENABLE = /\.(deltapatch|html?|ini)$/i
let openedFile: string | null = null
let windowTakesFiles = false
// the notifications on screen: kept, so a click on one still reaches its handler
const notes = new Set<Notification>()

function fileFrom(argv: string[]): string | null {
  for (const a of argv.slice(1)) {
    if (!a || a.startsWith('-') || !OPENABLE.test(a)) continue
    try {
      const full = resolve(a)
      if (existsSync(full) && statSync(full).isFile()) return full
    } catch {
      // not a file this can open
    }
  }
  return null
}

function openFile(path: string): void {
  if (captureDir) return
  if (win && windowTakesFiles) {
    win.webContents.send('app:open-file', path)
    showWindow()
  } else {
    openedFile = path
  }
}

function asset(name: string): string {
  return app.isPackaged ? join(process.resourcesPath, name) : join(__dirname, '..', '..', 'build', name)
}

function windowIcon(): string {
  return asset(process.platform === 'win32' ? 'icon.ico' : 'icon.png')
}

function createWindow(): void {
  win = new BrowserWindow({
    width: captureDir ? 1200 : 1120,
    height: captureDir ? 900 : 760,
    minWidth: 920,
    minHeight: 640,
    show: false,
    title: 'DeltaPatcher',
    backgroundColor: '#0b0f15',
    icon: windowIcon(),
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  })
  win.once('ready-to-show', () => {
    const hidden = hideFirstWindow && tray !== null
    hideFirstWindow = false
    if (!hidden) win?.show()
  })
  // links open in the browser; the window itself never navigates anywhere
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e) => e.preventDefault())
  win.on('close', (e) => {
    if (closeToTray && !quitting && tray) {
      e.preventDefault()
      win?.hide()
    }
  })
  // minimized to the tray: off the taskbar until the tray icon brings it back
  win.on('minimize', () => {
    if (minimizeToTray && tray) win?.hide()
  })
  win.on('closed', () => {
    win = null
  })
  if (devUrl) {
    win.loadURL(devUrl)
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

function createTray(): void {
  const image = nativeImage.createFromPath(asset(process.platform === 'win32' ? 'icon.ico' : 'icon.png'))
  if (image.isEmpty()) return
  tray = new Tray(process.platform === 'darwin' ? image.resize({ width: 18, height: 18 }) : image)
  tray.setToolTip('DeltaPatcher')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Show DeltaPatcher', click: () => showWindow() },
      { type: 'separator' },
      { label: 'Quit', click: () => { quitting = true; app.quit() } }
    ])
  )
  tray.on('click', () => showWindow())
}

function showWindow(): void {
  if (!win) {
    createWindow()
    return
  }
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

// only the app's own page may use the bridge
function fromOurPage(e: IpcMainInvokeEvent | IpcMainEvent): boolean {
  const url = e.senderFrame?.url ?? ''
  return url.startsWith('file://') || Boolean(devUrl && url.startsWith(devUrl))
}

// each handler names its own argument types
function handle(channel: string, fn: (e: IpcMainInvokeEvent, ...args: any[]) => unknown): void {
  ipcMain.handle(channel, (e, ...args) => {
    if (!fromOurPage(e)) throw new Error('refused')
    return fn(e, ...args)
  })
}

function registerIpc(): void {
  handle('engine:run', async (event, req: RunRequest) => {
    return runEngine(String(req.jobId), req.args, req.passphrase, (e) => {
      if (!event.sender.isDestroyed()) event.sender.send('engine:event', String(req.jobId), e)
    })
  })
  handle('engine:cancel', (_e, jobId: string) => cancelJob(String(jobId)))
  handle('engine:stop', (_e, jobId: string) => stopJob(String(jobId)))

  handle('dialog:folder', async (_e, start: string) => {
    if (!win) return null
    const r = await dialog.showOpenDialog(win, { properties: ['openDirectory', 'createDirectory'], defaultPath: start || undefined })
    return r.canceled ? null : r.filePaths[0]
  })
  handle('dialog:file', async (_e, start: string) => {
    if (!win) return null
    const r = await dialog.showOpenDialog(win, { properties: ['openFile', 'showHiddenFiles'], defaultPath: start || undefined })
    return r.canceled ? null : r.filePaths[0]
  })
  // Add a project file...: a project file from a publisher, or (advanced) a project's settings file
  handle('dialog:project-file', async (_e, start: string) => {
    if (!win) return null
    const r = await dialog.showOpenDialog(win, {
      properties: ['openFile'],
      defaultPath: start || undefined,
      filters: [
        { name: 'DeltaPatcher project files (*.deltapatch, *.html)', extensions: ['deltapatch', 'html', 'htm'] },
        { name: 'Settings files (*.ini)', extensions: ['ini'] },
        { name: 'All files', extensions: ['*'] }
      ]
    })
    return r.canceled ? null : r.filePaths[0]
  })
  // where to save a project file, with its type's extension
  handle('dialog:save-project-file', async (_e, name: string, ext: string) => {
    if (!win || (ext !== 'deltapatch' && ext !== 'html')) return null
    const r = await dialog.showSaveDialog(win, {
      defaultPath: `${String(name).replace(/[\\/:*?"<>|]/g, '')}.${ext}`,
      filters: [
        ext === 'html'
          ? { name: 'A page anyone can read in a browser (*.html)', extensions: ['html'] }
          : { name: 'DeltaPatcher project file (*.deltapatch)', extensions: ['deltapatch'] }
      ]
    })
    if (r.canceled || !r.filePath) return null
    return r.filePath.toLowerCase().endsWith(`.${ext}`) ? r.filePath : `${r.filePath}.${ext}`
  })
  // opens a folder, or a settings, log or text file: never a program
  handle('shell:open', async (_e, path: string) => {
    if (typeof path !== 'string' || !existsSync(path)) return 'not found'
    const isDir = statSync(path).isDirectory()
    if (!isDir && !/\.(ini|log|txt)$/i.test(path)) return 'refused'
    return shell.openPath(path)
  })
  // shows a file in its folder (a project's settings file), selected: nothing is opened or run
  handle('shell:show', (_e, path: string) => {
    if (typeof path !== 'string' || !existsSync(path)) return 'not found'
    shell.showItemInFolder(path)
    return ''
  })
  handle('clipboard:write', (_e, text: string) => clipboard.writeText(String(text)))
  // the file DeltaPatcher was started with, once; files that come later are sent as they come
  handle('app:take-opened-file', () => {
    windowTakesFiles = true
    const f = openedFile
    openedFile = null
    return f
  })
  // a desktop notification; onlyHidden: only while the window is not on screen (in the tray, or minimized)
  handle('app:notify', (_e, title: string, body: string, onlyHidden: boolean) => {
    if (!Notification.isSupported()) return 'unsupported'
    if (onlyHidden && win && win.isVisible() && !win.isMinimized()) return 'window shown'
    const n = new Notification({ title: String(title).slice(0, 120), body: String(body).slice(0, 400), icon: windowIcon() })
    notes.add(n)
    n.on('click', () => {
      notes.delete(n)
      showWindow()
      win?.webContents.send('app:show-page', 'projects')
    })
    n.on('close', () => notes.delete(n))
    n.show()
    return 'shown'
  })
  handle('app:minimize-to-tray', (_e, on: boolean) => {
    minimizeToTray = Boolean(on)
  })
  // start_with_system, put in place at every start and on every change: '' when done, else what went wrong
  handle('app:start-with-system', (_e, on: boolean) => {
    if (captureDir || startWithSystem === Boolean(on)) return ''
    const problem = applyStartWithSystem(system(), Boolean(on))
    if (!problem) startWithSystem = Boolean(on)
    return problem
  })
  // Reset: what the window keeps of its own (its web storage and cache) is cleared, and the window starts
  // afresh (a reload from here: the page itself may not navigate)
  handle('app:start-afresh', async () => {
    await session.defaultSession.clearStorageData()
    await session.defaultSession.clearCache()
    win?.webContents.reload()
    return true
  })
  handle('app:info', () => ({
    version: app.getVersion(),
    platform: process.platform,
    engine: engineCommand().describe,
    capture: Boolean(captureDir),
    install: installKind(),
    startWithSystem: unavailable(system())
  }))
  handle('update:install', (_e, file: string, kind: string) => installUpdate(String(file), String(kind)))
  handle('app:close-to-tray', (_e, on: boolean) => {
    closeToTray = Boolean(on)
  })

  // README screenshots: the window shows each page, says when it is ready, and is captured
  ipcMain.on('capture:ready', async (e, name: string) => {
    if (!win || !captureDir || !fromOurPage(e) || !/^[a-z]{1,20}$/.test(String(name))) return
    await new Promise((r) => setTimeout(r, 500))
    const img = await win.webContents.capturePage()
    mkdirSync(captureDir, { recursive: true })
    writeFileSync(join(captureDir, `app-${name}.png`), img.toPNG())
    win.webContents.send('capture:next')
  })
  ipcMain.on('capture:done', (e) => {
    if (!captureDir || !fromOurPage(e)) return
    quitting = true
    app.quit()
  })
}

// macOS hands a double-clicked file over as an event, possibly before the app is ready
app.on('open-file', (e, path) => {
  e.preventDefault()
  openFile(path)
})
openedFile = captureDir ? null : fileFrom(process.argv)

const single = captureDir ? true : app.requestSingleInstanceLock()
if (!single) {
  app.quit()
} else {
  app.on('second-instance', (_e, argv) => {
    const f = fileFrom(argv)
    if (f) openFile(f)
    else if (argv.includes(HIDDEN_ARG)) return
    showWindow()
  })
  app.whenReady().then(() => {
    if (process.platform !== 'darwin') Menu.setApplicationMenu(null)
    app.setAppUserModelId('io.github.deltapatcher')
    // the window needs no camera, microphone, notifications or anything else a page can ask for
    session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
    registerIpc()
    hideFirstWindow = !captureDir &&
      startedHidden(process.platform, process.argv, process.platform === 'darwin' && app.getLoginItemSettings().wasOpenedAtLogin)
    createWindow()
    if (!captureDir) createTray()
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
      else showWindow()
    })
  })
  app.on('before-quit', () => {
    quitting = true
    stopAll()
  })
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin' || captureDir) app.quit()
  })
}
