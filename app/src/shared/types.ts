// What passes between the engine (deltapatcher-cli --json), the main process and the window.

export type Style = 'ok' | 'warn' | 'err' | 'code' | 'head' | null

export interface LogEvent {
  event: 'log'
  text: string
  style: Style
}

export interface ProgressEvent {
  event: 'progress'
  done: number
  total: number
  label: string
}

export interface ServingEvent {
  event: 'serving'
  url: string
}

export type EngineEvent = LogEvent | ProgressEvent | ServingEvent

export interface EngineOutcome {
  ok: boolean
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>
  message?: string
  code?: string
}

export interface RunRequest {
  jobId: string
  args: string[]
  // the first line of the engine's standard input, for --passphrase-stdin; never on a command line
  passphrase?: string
}

// how this copy was installed, which decides how it updates itself (none: from source, or no way to)
export type InstallKind = 'setup' | 'zip' | 'appimage' | 'dmg' | 'none'

export interface AppInfo {
  version: string
  platform: string
  engine: string
  capture: boolean
  install: InstallKind
}

// the engine commands the window may run; anything else is refused by the main process
export const ENGINE_COMMANDS = ['update', 'rollback', 'publish', 'deploy', 'sign', 'ssh', 'config', 'version', 'serve', 'self-update'] as const
