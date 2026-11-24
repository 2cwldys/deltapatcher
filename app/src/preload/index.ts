// The only bridge between the window and the rest: a small, fixed API. The window never gets Node.
import { contextBridge, ipcRenderer, IpcRendererEvent, webUtils } from 'electron'
import { AppInfo, EngineEvent, EngineOutcome, ForceStopResult, RunRequest } from '../shared/types'

const api = {
  run: (req: RunRequest): Promise<EngineOutcome> => ipcRenderer.invoke('engine:run', req),
  cancel: (jobId: string): Promise<void> => ipcRenderer.invoke('engine:cancel', jobId),
  stop: (jobId: string): Promise<void> => ipcRenderer.invoke('engine:stop', jobId),
  onEvent: (cb: (jobId: string, e: EngineEvent) => void): (() => void) => {
    const listener = (_e: IpcRendererEvent, jobId: string, ev: EngineEvent): void => cb(jobId, ev)
    ipcRenderer.on('engine:event', listener)
    return () => ipcRenderer.removeListener('engine:event', listener)
  },
  pickFolder: (start: string): Promise<string | null> => ipcRenderer.invoke('dialog:folder', start),
  pickFile: (start: string): Promise<string | null> => ipcRenderer.invoke('dialog:file', start),
  pickProjectFile: (start: string): Promise<string | null> => ipcRenderer.invoke('dialog:project-file', start),
  saveProjectFile: (name: string, ext: string): Promise<string | null> => ipcRenderer.invoke('dialog:save-project-file', name, ext),
  // the path of a file dropped onto the window
  pathOf: (file: File): string => webUtils.getPathForFile(file),
  takeOpenedFile: (): Promise<string | null> => ipcRenderer.invoke('app:take-opened-file'),
  onOpenFile: (cb: (path: string) => void): void => {
    ipcRenderer.on('app:open-file', (_e: IpcRendererEvent, path: string) => cb(path))
  },
  onShowPage: (cb: (page: string) => void): void => {
    ipcRenderer.on('app:show-page', (_e: IpcRendererEvent, page: string) => cb(page))
  },
  notify: (title: string, body: string, onlyHidden: boolean): Promise<string> =>
    ipcRenderer.invoke('app:notify', title, body, onlyHidden),
  setMinimizeToTray: (on: boolean): Promise<void> => ipcRenderer.invoke('app:minimize-to-tray', on),
  // '' when the system is in step with the setting, else what went wrong
  setStartWithSystem: (on: boolean): Promise<string> => ipcRenderer.invoke('app:start-with-system', on),
  startAfresh: (): Promise<boolean> => ipcRenderer.invoke('app:start-afresh'),
  open: (path: string): Promise<string> => ipcRenderer.invoke('shell:open', path),
  show: (path: string): Promise<string> => ipcRenderer.invoke('shell:show', path),
  copy: (text: string): Promise<void> => ipcRenderer.invoke('clipboard:write', text),
  info: (): Promise<AppInfo> => ipcRenderer.invoke('app:info'),
  setCloseToTray: (on: boolean): Promise<void> => ipcRenderer.invoke('app:close-to-tray', on),
  installUpdate: (file: string, kind: string): Promise<string> => ipcRenderer.invoke('update:install', file, kind),
  // Force stop: on ends every run and starts none until it is turned off, also after a restart
  forceStop: (on: boolean): Promise<ForceStopResult> => ipcRenderer.invoke('app:force-stop', on),
  capture: {
    ready: (name: string): void => ipcRenderer.send('capture:ready', name),
    done: (): void => ipcRenderer.send('capture:done'),
    onNext: (cb: () => void): void => {
      ipcRenderer.on('capture:next', () => cb())
    }
  }
}

contextBridge.exposeInMainWorld('api', api)

export type Api = typeof api
