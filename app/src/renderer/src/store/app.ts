// The window's state: the projects and the one the pages show, its settings (through the engine), the
// activity log, the running job, and what each project's checks and updates on the Projects page are doing.
import { create } from 'zustand'
import type { EngineEvent, EngineOutcome, Style } from '@shared/types'
import { ask } from './modal'

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
export type Page = 'projects' | 'update' | 'release' | 'settings'

// a project as the engine lists it (projects --json)
export interface ProjectInfo {
  title: string
  file: string
  kind: 'main' | 'folder' | 'added' | 'given'
  ref: string
  missing: boolean
  current: boolean
  install_dir: string
  source_url: string
  publish_src: string
  where: string
  update_ready: boolean
  release_ready: boolean
}

// what an update (or a check) found and did
export interface UpdateResult {
  empty: boolean
  add: number
  replace: number
  remove: number
  same: number
  download_bytes: number
  download_blobs: number
  publisher_key: string
  kept: string[]
}

// a project on the Projects page: its check or update (on its own, or in Update all) and the last result
export interface ProjectRun {
  state: 'waiting' | 'checking' | 'updating' | 'done' | 'failed'
  check: boolean
  inAll: boolean
  jobId?: string
  progress?: Progress
  result?: UpdateResult
  message?: string
  stopped?: boolean
}

// Update all, or Check all: one engine job for every project that has a folder to update
export interface AllJob {
  jobId: string
  check: boolean
  total: number
  done: number
}

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
  mainPath: string
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
  // the last result of each action, shown by its page (the selected project's)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  results: Record<string, any>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  setResult: (kind: string, data: any) => void
  // a newer DeltaPatcher, when one is out (the sidebar offers it)
  appUpdate: { current: string; latest: string; url: string } | null
  checkAppUpdate: () => Promise<void>
  installAppUpdate: () => Promise<void>
  // the release served on this computer (Release, "This computer"): it keeps running whichever page is shown
  serving: { jobId: string; url: string } | null
  startServe: (store?: string) => Promise<void>
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

  // the projects; `project` is the settings file of the one Update, Release and Settings show
  projects: ProjectInfo[]
  project: string
  parallel: number
  loadProjects: () => Promise<void>
  selectProject: (file: string) => Promise<void>
  // a project command beside whatever else runs (projects, project new/add/rename/remove)
  side: (args: string[]) => Promise<EngineOutcome>
  // the Projects page: each project's check or update, by its file, and Update all / Check all
  runs: Record<string, ProjectRun>
  allJob: AllJob | null
  autoChecked: boolean
  runProject: (file: string, check: boolean) => Promise<void>
  runAll: (check: boolean) => Promise<void>
  stopAll: () => void
  noteResult: (file: string, result: UpdateResult, check: boolean) => void
}

let logId = 0
let jobSeq = 0
const handlers = new Map<string, (e: EngineEvent) => void>()
// the project a job's lines are about, for the console (a check or an update started on its row)
const jobTitles = new Map<string, string>()
// each project's last results, kept while another project is shown
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const resultsOf = new Map<string, Record<string, any>>()
// settings are saved one batch at a time: each save is its own engine run, and two at once could race
let pending: Record<string, Value> = {}
let pendingFor = ''
let saveTimer: ReturnType<typeof setTimeout> | null = null
let saving = false

// the commands that work on no single project; every other one gets the shown project's settings file
const NO_PROJECT = ['self-update', 'version', 'projects', 'project']

export function withProject(args: string[], file: string): string[] {
  if (!file || NO_PROJECT.includes(args[0]) || args.some((a) => a === '--all' || a.startsWith('--project'))) return args
  return [...args, `--project=${file}`]
}

export function running(r: ProjectRun | undefined): boolean {
  return Boolean(r && (r.state === 'waiting' || r.state === 'checking' || r.state === 'updating'))
}

async function flushSettings(log: (t: string, s?: Style) => void): Promise<void> {
  if (saving || Object.keys(pending).length === 0) return
  saving = true
  const batch = pending
  const file = pendingFor
  pending = {}
  const args = withProject(['config', 'set', ...Object.entries(batch).map(([k, v]) => `${k}=${toText(v)}`)], file)
  const r = await window.api.run({ jobId: `cfg-${++jobSeq}`, args })
  saving = false
  if (!r.ok) {
    log(`Settings not saved (tried again on the next change): ${r.message}`, 'err')
    pending = { ...batch, ...pending }
  } else if (Object.keys(pending).length) {
    flushSettings(log)
  }
}

// every change typed so far saved (before another project is shown)
async function settle(log: (t: string, s?: Style) => void): Promise<void> {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  for (let i = 0; i < 200 && saving; i++) await new Promise((r) => setTimeout(r, 50))
  if (Object.keys(pending).length) await flushSettings(log)
  for (let i = 0; i < 200 && saving; i++) await new Promise((r) => setTimeout(r, 50))
  if (Object.keys(pending).length) log('Some settings were not saved: type them again.', 'err')
  pending = {}
}

function now(): string {
  return new Date().toLocaleTimeString([], { hour12: false })
}

function toText(v: Value): string {
  return typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v)
}

function failure(err: unknown): EngineOutcome {
  return { ok: false, data: {}, message: String(err), code: 'failed' }
}

export const useApp = create<State>((set, get) => {
  // the shown project's settings, read again (at the start, and when another project is picked)
  const readSettings = async (): Promise<void> => {
    const r = await window.api.run({ jobId: `cfg-${++jobSeq}`, args: withProject(['config', 'get'], get().project) })
    if (!r.ok) {
      get().log(`Settings could not be read: ${r.message}`, 'err')
      set({ settings: {} })
      return
    }
    set({
      settings: r.data.settings ?? {},
      schema: r.data.schema ?? [],
      settingsPath: r.data.path ?? '',
      mainPath: r.data.main_path ?? r.data.path ?? '',
      logDir: r.data.log_dir ?? ''
    })
    window.api.setCloseToTray(Boolean(r.data.settings?.close_to_tray))
  }

  const putRun = (file: string, change: Partial<ProjectRun>): void => {
    const before = get().runs[file] ?? { state: 'done', check: false, inAll: false }
    set({ runs: { ...get().runs, [file]: { ...before, ...change } } })
  }

  return {
    page: 'update',
    settings: {},
    schema: [],
    settingsPath: '',
    mainPath: '',
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
    appUpdate: null,
    projects: [],
    project: '',
    parallel: 3,
    runs: {},
    allJob: null,
    autoChecked: false,

    // GitHub's newest release against this version, by number; quiet, and nothing when offline
    checkAppUpdate: async () => {
      const r = await get().run('', ['self-update', '--check'], { quiet: true })
      if (r.ok && r.data.newer) {
        set({ appUpdate: { current: String(r.data.current), latest: String(r.data.latest), url: String(r.data.url) } })
      }
    },

    // download the new version's file for this system and hand it to the main process, which installs it and
    // closes DeltaPatcher; a copy that cannot replace itself (the zip, from source) opens the release page
    installAppUpdate: async () => {
      const u = get().appUpdate
      if (!u) return
      const info = await window.api.info()
      if (info.install === 'zip' || info.install === 'none') {
        const go = await ask.confirm(
          `DeltaPatcher ${u.latest} is out`,
          `You have ${u.current}. This copy cannot replace itself (${info.install === 'zip' ? 'it runs from the zip' : 'it runs from source'}): ` +
            'download the new one from the release page.',
          'Open the release page'
        )
        if (go) window.open(u.url, '_blank')
        return
      }
      const how =
        info.install === 'setup'
          ? 'It downloads the new setup, starts it, and closes DeltaPatcher so it can install.'
          : info.install === 'appimage'
            ? 'It downloads the new AppImage, puts it in place of this one, and starts it.'
            : 'It downloads the new disk image and opens it: drag DeltaPatcher into Applications, replacing the old one.'
      if (!(await ask.confirm(`Update to DeltaPatcher ${u.latest}?`, `You have ${u.current}. ${how}`, 'Update'))) return
      const r = await get().run(`Download DeltaPatcher ${u.latest}`, ['self-update', `--app=${info.install}`])
      if (!r.ok || !r.data.file) return
      const done = await window.api.installUpdate(String(r.data.file), info.install)
      if (done !== 'started') await ask.info('The update was not started', 'Download it from the release page instead.', u.url)
    },

    // serve a release on this computer: the store a release just wrote, else the saved store, else the engine's
    // own store for the saved folder
    startServe: async (store?: string) => {
      if (get().serving) return
      const chosen = store || String(get().effective('store_dir') || '')
      const port = String(get().effective('serve_port') || 8000)
      if (!chosen && !get().settings.publish_src) {
        await ask.info(
          'Nothing to serve yet',
          `"This computer" serves your release at http://localhost:${port}/, for this computer only, so you can ` +
            'try it on the Update page before players get it.\n\nChoose the folder to release, then click Release.'
        )
        return
      }
      const jobId = `serve-${++jobSeq}`
      handlers.set(jobId, (e) => {
        if (e.event === 'serving') set({ serving: { jobId, url: e.url } })
      })
      set({ serving: { jobId, url: '' } })
      const args = chosen ? ['serve', chosen, `--port=${port}`] : ['serve', `--port=${port}`]
      const r = await window.api.run({ jobId, args: withProject(args, get().project) })
      const started = Boolean(get().serving?.url)
      handlers.delete(jobId)
      set({ serving: null })
      if (!r.ok && r.code !== 'engine-stopped') {
        get().log(`Serving stopped: ${r.message}`, 'err')
        // it never started (nothing published there yet, the port is taken): say why, not only in the console
        if (!started) await ask.info('The store could not be served', r.message ?? 'The engine gave no reason.')
      }
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

    // the projects first (which one to show), then its settings; with several projects the window opens on
    // the Projects page, with one it opens on Update as it always did
    load: async () => {
      await get().loadProjects()
      await readSettings()
      set({ loaded: true, page: get().projects.length > 1 ? 'projects' : get().page })
    },

    loadProjects: async () => {
      const r = await get().side(['projects'])
      if (!r.ok) {
        get().log(`The projects could not be read: ${r.message}`, 'err')
        return
      }
      const list = (r.data.projects ?? []) as ProjectInfo[]
      const shown = list.find((p) => p.file === get().project && !p.missing)
      set({
        projects: list,
        mainPath: String(r.data.main ?? get().mainPath),
        parallel: Math.max(1, Number(r.data.parallel_updates ?? 3)),
        project: shown ? shown.file : String(r.data.current ?? list[0]?.file ?? '')
      })
    },

    // show another project: what was typed is saved first, its results are kept for when it is shown again,
    // and DeltaPatcher reopens on the new one next time
    selectProject: async (file) => {
      const before = get().project
      if (!file || file === before) return
      await settle(get().log)
      resultsOf.set(before, get().results)
      set({ project: file, results: resultsOf.get(file) ?? {} })
      const p = get().projects.find((x) => x.file === file)
      await get().side(['config', 'set', `current_project=${p ? p.ref : file}`])
      await readSettings()
    },

    side: async (args) => {
      try {
        return await window.api.run({ jobId: `side-${++jobSeq}`, args })
      } catch (err) {
        return failure(err)
      }
    },

    // a field changed: the window shows it at once, the project's settings file gets it when typing pauses
    set: (key, value) => {
      set({ settings: { ...get().settings, [key]: value } })
      if (key === 'close_to_tray') window.api.setCloseToTray(Boolean(value))
      if (Object.keys(pending).length === 0) pendingFor = get().project
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
        r = await window.api.run({ jobId, args: withProject(args, get().project), passphrase: opts.passphrase })
      } catch (err) {
        r = failure(err)
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

    toggleConsole: () => set({ consoleOpen: !get().consoleOpen }),

    // a check or an update of one project, from its row: an engine job of its own, beside any other
    runProject: async (file, check) => {
      const p = get().projects.find((x) => x.file === file)
      if (!p || running(get().runs[file])) return
      const jobId = `prj-${++jobSeq}`
      putRun(file, { state: check ? 'checking' : 'updating', check, inAll: false, jobId, progress: undefined, message: undefined, stopped: false })
      jobTitles.set(jobId, p.title)
      handlers.set(jobId, (e) => {
        if (e.event === 'progress') putRun(file, { progress: { done: e.done, total: e.total, label: e.label } })
      })
      let r: EngineOutcome
      try {
        r = await window.api.run({ jobId, args: check ? ['update', `--project=${file}`, '--check'] : ['update', `--project=${file}`] })
      } catch (err) {
        r = failure(err)
      }
      handlers.delete(jobId)
      jobTitles.delete(jobId)
      if (r.ok) {
        putRun(file, { state: 'done', jobId: undefined, progress: undefined, result: r.data as UpdateResult })
      } else {
        get().log(`[${p.title}] ${r.message ?? 'failed'}`, 'err')
        putRun(file, { state: 'failed', jobId: undefined, progress: undefined, message: r.message, stopped: r.code === 'stopped' })
      }
    },

    // every project with a folder to update, several at the same time, in one engine job; each one's
    // progress and result come tagged with its file
    runAll: async (check) => {
      if (get().allJob) return
      const jobId = `all-${++jobSeq}`
      const label = check ? 'Check all' : 'Update all'
      set({ allJob: { jobId, check, total: 0, done: 0 } })
      if (!get().busy) set({ status: `${label}...`, statusKind: 'busy' })
      get().log(`== ${label}`, 'head')
      handlers.set(jobId, (e) => {
        if (e.event === 'queue') {
          const runs = { ...get().runs }
          for (const q of e.run) {
            runs[q.file] = { ...(runs[q.file] ?? {}), state: 'waiting', check, inAll: true, progress: undefined, message: undefined, stopped: false }
          }
          const all = get().allJob
          set({ runs, allJob: all ? { ...all, total: e.run.length } : all })
        } else if (e.event === 'project-start') {
          putRun(e.file, { state: check ? 'checking' : 'updating' })
        } else if (e.event === 'progress' && e.file) {
          putRun(e.file, { progress: { done: e.done, total: e.total, label: e.label } })
        } else if (e.event === 'project-end') {
          if (e.ok) {
            putRun(e.file, { state: 'done', inAll: false, check, progress: undefined, message: undefined, result: e.data as UpdateResult })
          } else {
            putRun(e.file, { state: 'failed', inAll: false, check, progress: undefined, message: e.message, stopped: e.code === 'stopped' })
          }
          const all = get().allJob
          if (all) set({ allJob: { ...all, done: all.done + 1 } })
        }
      })
      let r: EngineOutcome
      try {
        r = await window.api.run({ jobId, args: check ? ['update', '--all', '--check'] : ['update', '--all'] })
      } catch (err) {
        r = failure(err)
      }
      handlers.delete(jobId)
      // a project the run never finished (the engine itself stopped): failed, with the run's reason
      const runs = { ...get().runs }
      for (const [file, run] of Object.entries(runs)) {
        if (run.inAll) {
          runs[file] = { ...run, inAll: false, state: 'failed', progress: undefined, message: r.ok ? 'Not finished.' : r.message, stopped: r.code === 'stopped' }
        }
      }
      const total = get().allJob?.total ?? 0
      set({ runs, allJob: null })
      if (!r.ok) get().log(r.message ?? 'failed', 'err')
      if (!get().busy) {
        if (r.ok) set({ status: `${label} done.`, statusKind: 'ok', progress: { done: total, total, label: 'projects' } })
        else set({ status: r.code === 'stopped' ? `${label} stopped.` : `${label}: ${r.message}`, statusKind: 'err', progress: null })
      }
    },

    stopAll: () => {
      const all = get().allJob
      if (!all) return
      window.api.cancel(all.jobId)
      if (!get().busy) set({ status: 'Stopping...' })
    },

    // an update or a check on the Update page: its project's row shows it too
    noteResult: (file, result, check) => {
      if (!running(get().runs[file])) putRun(file, { state: 'done', check, inAll: false, result, message: undefined })
    }
  }
})

// one listener for every job's events: a job's own handler first, then the log and the progress bar
window.api.onEvent((jobId, e) => {
  const h = handlers.get(jobId)
  if (h) h(e)
  const st = useApp.getState()
  if (e.event === 'log') {
    const who = e.project ?? jobTitles.get(jobId)
    st.log(who ? `[${who}] ${e.text}` : e.text, e.style)
  } else if (e.event === 'progress' && jobId === st.jobId) {
    useApp.setState({ progress: { done: e.done, total: e.total, label: e.label } })
  }
})

export function newJobId(prefix: string): string {
  return `${prefix}-${++jobSeq}`
}
