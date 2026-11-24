// The window's state: settings (deltapatcher.ini, through the engine), the activity log, the running job.
import { create } from 'zustand'
import type { EngineEvent, EngineOutcome, Style } from '@shared/types'

export type Value = string | number | boolean
export interface SchemaItem {
  section: string
  key: string
  default: Value
  what: string
}
export interface LogLine {
  id: number
  time: string
  text: string
  style: Style
}
export interface Progress {
  done: number
  total: number
  label: string
}
export type Page = 'update' | 'publish' | 'server' | 'settings'

interface RunOptions {
  passphrase?: string
  cancellable?: boolean
  quiet?: boolean
  onEvent?: (e: EngineEvent) => void
}

interface State {
  page: Page
  settings: Record<string, Value>
  schema: SchemaItem[]
  settingsPath: string
  logDir: string
  loaded: boolean
  logs: LogLine[]
  progress: Progress | null
  busy: string | null
  jobId: string | null
  cancellable: boolean
  status: string
  statusKind: 'idle' | 'ok' | 'err' | 'busy'
  consoleOpen: boolean
  // the last result of each action, shown by its page
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  results: Record<string, any>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  setResult: (kind: string, data: any) => void
  // the local server ("Serve locally"): it keeps running whichever page is shown
  serving: { jobId: string; url: string } | null
  startServe: () => Promise<void>
  stopServe: () => void
  setPage: (p: Page) => void
  load: () => Promise<void>
  set: (key: string, value: Value) => void
  // a setting's value, or its default when the field is empty (what the engine itself would use)
  effective: (key: string) => Value
  run: (label: string, args: string[], opts?: RunOptions) => Promise<EngineOutcome>
  log: (text: string, style?: Style) => void
  cancel: () => void
  toggleConsole: () => void
}

let logId = 0
let jobSeq = 0
const handlers = new Map<string, (e: EngineEvent) => void>()
// settings are saved one batch at a time: each save is its own engine run, and two at once could race
let pending: Record<string, Value> = {}
let saveTimer: ReturnType<typeof setTimeout> | null = null
let saving = false

async function flushSettings(log: (t: string, s?: Style) => void): Promise<void> {
  if (saving || Object.keys(pending).length === 0) return
  saving = true
  const batch = pending
  pending = {}
  const args = ['config', 'set', ...Object.entries(batch).map(([k, v]) => `${k}=${toText(v)}`)]
  const r = await window.api.run({ jobId: `cfg-${++jobSeq}`, args })
  saving = false
  if (!r.ok) {
    log(`Settings not saved (tried again on the next change): ${r.message}`, 'err')
    pending = { ...batch, ...pending }
  } else if (Object.keys(pending).length) {
    flushSettings(log)
  }
}

function now(): string {
  return new Date().toLocaleTimeString([], { hour12: false })
}

function toText(v: Value): string {
  return typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v)
}

export const useApp = create<State>((set, get) => ({
  page: 'update',
  settings: {},
  schema: [],
  settingsPath: '',
  logDir: '',
  loaded: false,
  logs: [],
  progress: null,
  busy: null,
  jobId: null,
  cancellable: false,
  status: 'Ready.',
  statusKind: 'idle',
  consoleOpen: false,
  results: {},
  serving: null,

  startServe: async () => {
    if (get().serving) return
    const store = String(get().effective('store_dir') || '')
    if (!store) return
    const jobId = `serve-${++jobSeq}`
    const port = String(get().effective('serve_port') || 8000)
    handlers.set(jobId, (e) => {
      if (e.event === 'serving') set({ serving: { jobId, url: e.url } })
    })
    set({ serving: { jobId, url: '' } })
    const r = await window.api.run({ jobId, args: ['serve', store, `--port=${port}`] })
    handlers.delete(jobId)
    set({ serving: null })
    if (!r.ok && r.code !== 'engine-stopped') get().log(`Serving stopped: ${r.message}`, 'err')
  },

  stopServe: () => {
    const s = get().serving
    if (s) window.api.stop(s.jobId)
  },

  effective: (key) => {
    const v = get().settings[key]
    if (v !== undefined && v !== null && v !== '') return v
    const d = get().schema.find((s) => s.key === key)
    return d ? d.default : ''
  },

  setResult: (kind, data) => set({ results: { ...get().results, [kind]: data } }),
  setPage: (p) => set({ page: p }),

  load: async () => {
    const r = await window.api.run({ jobId: `cfg-${++jobSeq}`, args: ['config', 'get'] })
    if (!r.ok) {
      get().log(`Settings could not be read: ${r.message}`, 'err')
      set({ loaded: true })
      return
    }
    set({
      settings: r.data.settings ?? {},
      schema: r.data.schema ?? [],
      settingsPath: r.data.path ?? '',
      logDir: r.data.log_dir ?? '',
      loaded: true
    })
    window.api.setCloseToTray(Boolean(r.data.settings?.close_to_tray))
  },

  // a field changed: the window shows it at once, deltapatcher.ini gets it when typing pauses
  set: (key, value) => {
    set({ settings: { ...get().settings, [key]: value } })
    if (key === 'close_to_tray') window.api.setCloseToTray(Boolean(value))
    pending[key] = value
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => flushSettings(get().log), 500)
  },

  run: async (label, args, opts = {}) => {
    const jobId = `job-${++jobSeq}`
    if (opts.onEvent) handlers.set(jobId, opts.onEvent)
    if (!opts.quiet) {
      set({ busy: label, jobId, cancellable: Boolean(opts.cancellable), progress: null, status: `${label}...`, statusKind: 'busy' })
      get().log(`== ${label}`, 'head')
    }
    let r: EngineOutcome
    try {
      r = await window.api.run({ jobId, args, passphrase: opts.passphrase })
    } catch (err) {
      r = { ok: false, data: {}, message: String(err), code: 'failed' }
    }
    handlers.delete(jobId)
    if (!opts.quiet) {
      if (r.ok) {
        set({ status: `${label} done.`, statusKind: 'ok' })
      } else {
        get().log(r.message ?? 'failed', 'err')
        set({ status: r.code === 'stopped' ? `${label} stopped.` : `${label} failed: ${r.message}`, statusKind: 'err', progress: null })
      }
      set({ busy: null, jobId: null, cancellable: false })
    }
    return r
  },

  log: (text, style = null) => {
    const lines = text.split('\n').map((t) => ({ id: ++logId, time: now(), text: t, style }))
    set({ logs: [...get().logs, ...lines].slice(-2000) })
  },

  cancel: () => {
    const id = get().jobId
    if (id) {
      window.api.cancel(id)
      set({ status: 'Stopping...' })
    }
  },

  toggleConsole: () => set({ consoleOpen: !get().consoleOpen })
}))

// one listener for every job's events: a job's own handler first, then the log and the progress bar
window.api.onEvent((jobId, e) => {
  const h = handlers.get(jobId)
  if (h) h(e)
  const st = useApp.getState()
  if (e.event === 'log') st.log(e.text, e.style)
  else if (e.event === 'progress' && jobId === st.jobId) useApp.setState({ progress: { done: e.done, total: e.total, label: e.label } })
})

export function newJobId(prefix: string): string {
  return `${prefix}-${++jobSeq}`
}
