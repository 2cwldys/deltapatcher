// The only bridge between the window and the rest: a small, fixed API. The window never gets Node.
import { contextBridge, ipcRenderer, IpcRendererEvent } from 'electron'
import { AppInfo, EngineEvent, EngineOutcome, RunRequest } from '../shared/types'

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
  open: (path: string): Promise<string> => ipcRenderer.invoke('shell:open', path),
  copy: (text: string): Promise<void> => ipcRenderer.invoke('clipboard:write', text),
  info: (): Promise<AppInfo> => ipcRenderer.invoke('app:info'),
  setCloseToTray: (on: boolean): Promise<void> => ipcRenderer.invoke('app:close-to-tray', on),
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
