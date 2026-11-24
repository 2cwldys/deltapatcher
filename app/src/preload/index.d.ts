import type { AppInfo, EngineEvent, EngineOutcome, RunRequest } from '../shared/types'

declare global {
  interface Window {
    api: {
      run: (req: RunRequest) => Promise<EngineOutcome>
      cancel: (jobId: string) => Promise<void>
      stop: (jobId: string) => Promise<void>
      onEvent: (cb: (jobId: string, e: EngineEvent) => void) => () => void
      pickFolder: (start: string) => Promise<string | null>
      pickFile: (start: string) => Promise<string | null>
      open: (path: string) => Promise<string>
      show: (path: string) => Promise<string>
      copy: (text: string) => Promise<void>
      info: () => Promise<AppInfo>
      setCloseToTray: (on: boolean) => Promise<void>
      installUpdate: (file: string, kind: string) => Promise<string>
      capture: {
        ready: (name: string) => void
        done: () => void
        onNext: (cb: () => void) => void
      }
    }
  }
}

export {}
