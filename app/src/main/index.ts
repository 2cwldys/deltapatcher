// The DeltaPatcher app: one window, a tray icon, and the engine behind both.
import {
  app,
  BrowserWindow,
  IpcMainEvent,
  IpcMainInvokeEvent,
  Menu,
  Tray,
  clipboard,
  dialog,
  ipcMain,
  nativeImage,
  session,
  shell
} from 'electron'
import { existsSync, mkdirSync, statSync, writeFileSync } from 'fs'
import { join, resolve } from 'path'
import { RunRequest } from '../shared/types'
import { cancelJob, engineCommand, runEngine, stopAll, stopJob } from './engine'

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
  win.once('ready-to-show', () => win?.show())
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
  // opens a folder, or a settings, log or text file: never a program
  handle('shell:open', async (_e, path: string) => {
    if (typeof path !== 'string' || !existsSync(path)) return 'not found'
    const isDir = statSync(path).isDirectory()
    if (!isDir && !/\.(ini|log|txt)$/i.test(path)) return 'refused'
    return shell.openPath(path)
  })
  handle('clipboard:write', (_e, text: string) => clipboard.writeText(String(text)))
  handle('app:info', () => ({
    version: app.getVersion(),
    platform: process.platform,
    engine: engineCommand().describe,
    capture: Boolean(captureDir)
  }))
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

const single = captureDir ? true : app.requestSingleInstanceLock()
if (!single) {
  app.quit()
} else {
  app.on('second-instance', () => showWindow())
  app.whenReady().then(() => {
    if (process.platform !== 'darwin') Menu.setApplicationMenu(null)
    app.setAppUserModelId('io.github.deltapatcher')
    // the window needs no camera, microphone, notifications or anything else a page can ask for
    session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
    registerIpc()
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
