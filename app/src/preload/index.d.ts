import type { AppInfo, EngineEvent, EngineOutcome, ForceStopResult, RunRequest } from '../shared/types'

declare global {
  interface Window {
    api: {
      run: (req: RunRequest) => Promise<EngineOutcome>
      cancel: (jobId: string) => Promise<void>
      stop: (jobId: string) => Promise<void>
      onEvent: (cb: (jobId: string, e: EngineEvent) => void) => () => void
      pickFolder: (start: string) => Promise<string | null>
      pickFile: (start: string) => Promise<string | null>
      pickProjectFile: (start: string) => Promise<string | null>
      saveProjectFile: (name: string, ext: string) => Promise<string | null>
      pathOf: (file: File) => string
      takeOpenedFile: () => Promise<string | null>
      onOpenFile: (cb: (path: string) => void) => void
      onShowPage: (cb: (page: string) => void) => void
      notify: (title: string, body: string, onlyHidden: boolean) => Promise<string>
      setMinimizeToTray: (on: boolean) => Promise<void>
      setSeeding: (count: number) => Promise<void>
      setStartWithSystem: (on: boolean) => Promise<string>
      startAfresh: () => Promise<boolean>
      open: (path: string) => Promise<string>
      show: (path: string) => Promise<string>
      copy: (text: string) => Promise<void>
      info: () => Promise<AppInfo>
      setCloseToTray: (on: boolean) => Promise<void>
      installUpdate: (file: string, kind: string) => Promise<string>
      forceStop: (on: boolean) => Promise<ForceStopResult>
      capture: {
        ready: (name: string) => void
        done: () => void
        onNext: (cb: () => void) => void
      }
    }
  }
}

export {}
