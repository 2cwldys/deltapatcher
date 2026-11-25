// The window's state: the projects and the one the pages show, its settings (through the engine), the
// activity log, the running job, and what each project's checks and updates on the Projects page are doing.
import { create } from 'zustand'
import type { EngineEvent, EngineOutcome, Style } from '@shared/types'
import { FORCE_STOPPED } from '@shared/forceStop'
import { ask, useModal } from './modal'
import { anotherRun, restartSchedules } from './schedule'
import { autoOutcome, madeAt, nextBackup } from './backups'
import { windowStorage } from './folds'
import { closeWelcome, welcomeAtStart } from './welcome'
import { PublisherChange, ReleaseView, settled, withoutPrompt } from './publisher'
import type { ExportType, HostKeys, PrivateChange, PrivatePart } from './privateFiles'
import {
  afterRun, PLACE_KEYS, RESTART_KEYS, SEED_KEYS, SeedRow, seedingCount, seedOffArgs, TimedRow, torrentsRefused, wantsSeeding,
  withRow
} from './seeding'

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
  description: string
  website: string
  // where it goes for players, and the folder it goes into inside the one they pick
  install_hint: string
  install_folder: string
  // how often the app backs it up (0: never), when its newest backup was made, and whether it has a destination
  backup_every_hours: number
  backup_last: string
  backup_ready: boolean
  // the http(s) address a project file gives for it ('' when it has none)
  address: string
  update_ready: boolean
  release_ready: boolean
  // it asks when its publisher changes it; its releases share its details and show file names
  follow_publisher?: boolean
  share_updates?: boolean
  show_files?: boolean
  // shared by torrent (by torrent only: it has no address), and set to seed
  torrent?: boolean
  torrent_only?: boolean
  seed?: boolean
  // its releases are signed (sign = yes): what a private project file needs
  sign?: boolean
}

// a project of a project file being imported: what the file says, and what the person chose. From an update
// capable file, `updates` is the project here it updates (unless the person adds it as a new one), with what
// that changes, or older: the project has newer details than the file
export interface ImportEntry {
  title: string
  source_url: string
  trust_key?: string
  description?: string
  website?: string
  install_hint?: string
  install_folder?: string
  taken: boolean
  pick: boolean
  name: string
  folder: string
  updates?: { project: string; file: string }
  change?: PublisherChange | null
  older?: boolean
  asNew: boolean
  // how its publisher shares it by torrent, when it does; seed: the person's yes to seeding it, off at first
  torrent?: { id: string; name: string; seeds: string[] }
  seed: boolean
  // a private file's project (store/privateFiles.ts): what releasing it needs, the server's host keys, what it
  // changes in the project here it updates (a file older than the one applied to it, older, shows both times, made
  // and applied_made, and is applied only with the person's Apply it anyway: applyOlder), a project this computer
  // only plays, or releases with a signing key DeltaPatcher does not know yet (never changed by the file); and this
  // computer's own folders and keys, as the person gives them
  private?: PrivatePart
  where?: string
  host_keys?: HostKeys
  changes?: PrivateChange[]
  made?: string
  applied_made?: string
  applyOlder?: boolean
  player_here?: { project: string; file: string }
  key_unknown?: { project: string; file: string }
  publishSrc: string
  signingKey: string
  sshKey: string
  deployFolder: string
  trustHostKeys: boolean
}

export interface Importing {
  file: string
  signed: boolean
  signer: string
  fingerprint: string
  // an update capable file: its projects follow their publisher
  updates: boolean
  // torrents are refused here, and the file shares a project by torrent
  torrentsRefused: boolean
  entries: ImportEntry[]
  updateNow: boolean
  working: boolean
  // a publisher's private file, which sets up releasing; asPlayer: the person adds it as a player instead
  private: boolean
  asPlayer: boolean
}

// the export dialog: public (for players) or private (for the publisher, on any computer), the projects ticked,
// the kind of file, update capable or a new project for every file, and the signing key
export interface Exporting {
  type: ExportType
  picks: Record<string, boolean>
  kind: 'deltapatch' | 'html'
  updates: boolean
  sign: boolean
  key: string
  keyExists: boolean
  keyLocked: boolean
}

// what an update (or a check) found and did; with it, what the release says for players (store/publisher.ts)
export interface UpdateResult extends ReleaseView {
  empty: boolean
  add: number
  replace: number
  remove: number
  same: number
  download_bytes: number
  download_blobs: number
  // what it fetches: less than download_bytes when archives are rebuilt from what the folder has (store/download.ts)
  fetch_bytes?: number
  rebuilt?: number
  publisher_key: string
  kept: string[]
}

// the "What changed" dialog: the project it is about and the result it shows
export interface ChangesView {
  title: string
  result: UpdateResult
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
  // the welcome screen, over the pages until it is closed or a page is picked (store/welcome.ts)
  welcome: boolean
  showWelcome: (on: boolean) => void
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
  // a project command beside whatever else runs (projects, project new/add/rename/remove/import/export)
  side: (args: string[], passphrase?: string) => Promise<EngineOutcome>
  // the import and export dialogs of project files
  importing: Importing | null
  exporting: Exporting | null
  // the "What changed" dialog, while it is open
  changesView: ChangesView | null
  // the release notes typed on the Release page, by project file, until that project's release is sent
  notes: Record<string, string>
  setNotes: (file: string, text: string) => void
  // Apply or Not now answered a project's prompt: it goes from its row and from the Update page
  settlePrompt: (file: string) => void
  // the Projects page: each project's check or update, by its file, and Update all / Check all
  runs: Record<string, ProjectRun>
  allJob: AllJob | null
  // the project the app is backing up by itself (backup_every_hours), while it does
  backupJob: string | null
  // why the last backup of a project failed, by its file (the Backups card shows it); gone once one works
  backupProblems: Record<string, string>
  autoChecked: boolean
  runProject: (file: string, check: boolean) => Promise<void>
  // auto: a check (or an update) DeltaPatcher started by itself, which may notify or update on its own
  runAll: (check: boolean, auto?: boolean) => Promise<void>
  stopAll: () => void
  noteResult: (file: string, result: UpdateResult, check: boolean) => void

  // Force stop: every run ended, none starts, no dialog opens and the pages are greyed out until it is
  // lifted, also after a restart; stoppedSince is when it was turned on (an ISO time, '' when not known)
  stopped: boolean
  stoppedSince: string
  forceStop: () => Promise<void>
  liftStop: () => Promise<void>

  // Seeding (store/seeding.ts): the seed run while it goes, which never makes the window busy; each project's
  // row from it, by settings file; why seeding is not running ('' when it runs, or nothing asks for it); the
  // projects the last Force stop turned seeding off for
  seeding: { jobId: string; since: number } | null
  seedRows: Record<string, TimedRow>
  seedWhy: string
  seedTurnedOff: string[]
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
// the projects a notification has named as having an update, until they are up to date again
const notified = new Set<string>()
// the project picked last: when two picks overlap, the last one wins
let pickedLast = ''

// the commands that work on no single project; every other one gets the shown project's settings file
const NO_PROJECT = ['self-update', 'version', 'projects', 'project']

// the status bar while Force stop is on
const STOPPED_STATUS = 'Force stop is on: nothing runs until it is lifted.'

export function withProject(args: string[], file: string): string[] {
  if (!file || NO_PROJECT.includes(args[0]) || args.some((a) => a === '--all' || a.startsWith('--project'))) return args
  return [...args, `--project=${file}`]
}

export function running(r: ProjectRun | undefined): boolean {
  return Boolean(r && (r.state === 'waiting' || r.state === 'checking' || r.state === 'updating'))
}

// the system put in step with start_with_system: at every start, and when the switch changes
async function startWithSystem(on: boolean, log: (t: string, s?: Style) => void): Promise<void> {
  const problem = await window.api.setStartWithSystem(on)
  if (problem) log(`Starting with the computer could not be ${on ? 'set up' : 'turned off'}: ${problem}`, 'err')
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
  if (r.ok) void seedSettingsChanged(Object.keys(batch))
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
  // the shown project's settings, read again (at the start); with `file`, another project's, shown together
  // with them, so a page never names one project and shows another's settings
  const readSettings = async (file?: string): Promise<void> => {
    const r = await window.api.run({ jobId: `cfg-${++jobSeq}`, args: withProject(['config', 'get'], file ?? get().project) })
    if (file !== undefined && file !== pickedLast) return
    const shown = file === undefined ? {} : { project: file, results: resultsOf.get(file) ?? {} }
    if (!r.ok) {
      get().log(`Settings could not be read: ${r.message}`, 'err')
      set({ ...shown, settings: {} })
      return
    }
    set({
      ...shown,
      settings: r.data.settings ?? {},
      schema: r.data.schema ?? [],
      settingsPath: r.data.path ?? '',
      mainPath: r.data.main_path ?? r.data.path ?? '',
      logDir: r.data.log_dir ?? ''
    })
    window.api.setCloseToTray(Boolean(r.data.settings?.close_to_tray))
    window.api.setMinimizeToTray(Boolean(r.data.settings?.minimize_to_tray))
    void startWithSystem(Boolean(r.data.settings?.start_with_system), get().log)
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
    backupJob: null,
    backupProblems: {},
    autoChecked: false,
    importing: null,
    exporting: null,
    changesView: null,
    notes: {},
    stopped: false,
    stoppedSince: '',
    welcome: false,
    seeding: null,
    seedRows: {},
    seedWhy: '',
    seedTurnedOff: [],

    setNotes: (file, text) => set({ notes: { ...get().notes, [file]: text } }),

    settlePrompt: (file) => {
      set(settled(get().runs, get().results, get().project, file))
      const kept = resultsOf.get(file)
      if (kept?.update) resultsOf.set(file, { ...kept, update: withoutPrompt(kept.update) })
    },

    // GitHub's newest release against this version, by number; quiet, and nothing when offline
    checkAppUpdate: async () => {
      if (get().stopped) return
      const r = await get().run('', ['self-update', '--check'], { quiet: true })
      if (r.ok && r.data.newer) {
        set({ appUpdate: { current: String(r.data.current), latest: String(r.data.latest), url: String(r.data.url) } })
      }
    },

    // download the new version's file for this system and hand it to the main process, which installs it and
    // closes DeltaPatcher; a copy that cannot replace itself (the zip, from source) opens the release page
    installAppUpdate: async () => {
      const u = get().appUpdate
      if (!u || get().stopped) return
      const info = await window.api.info()
      if (info.install === 'zip' || info.install === 'portable' || info.install === 'none') {
        const how = info.install === 'zip' ? 'it runs from the zip' : info.install === 'portable' ? 'it is the portable program' : 'it runs from source'
        const go = await ask.confirm(
          `DeltaPatcher ${u.latest} is out`,
          `You have ${u.current}. This copy cannot replace itself (${how}): ` +
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
      if (!r.ok || !r.data.file || get().stopped) return
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
      if (!r.ok && r.code !== 'engine-stopped' && r.code !== FORCE_STOPPED) {
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
    // a page picked: the welcome screen gives way to it
    setPage: (p) => set({ page: p, welcome: false }),
    // closed by hand: it does not come back at the next start ("Getting started" opens it again)
    showWelcome: (on) => {
      if (!on) closeWelcome(windowStorage())
      set({ welcome: on })
    },

    // the projects first (which one to show), then its settings; with several projects the window opens on
    // the Projects page, with one it opens on Update as it always did; while no project is set up yet, the
    // welcome screen shows over it
    load: async () => {
      // a Force stop left on holds: the window opens stopped
      const fs = (await window.api.info()).forceStop
      if (fs.on) {
        useModal.getState().block(true)
        set({ stopped: true, stoppedSince: fs.since, status: STOPPED_STATUS, statusKind: 'err' })
        get().log(STOPPED_STATUS, 'warn')
      }
      await get().loadProjects()
      await readSettings()
      set({
        loaded: true,
        page: get().projects.length > 1 ? 'projects' : get().page,
        welcome: get().projects.length > 0 && welcomeAtStart(get().projects, windowStorage())
      })
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
      pickedLast = file
      await settle(get().log)
      resultsOf.set(before, get().results)
      await readSettings(file)
      const p = get().projects.find((x) => x.file === file)
      await get().side(['config', 'set', `current_project=${p ? p.ref : file}`])
    },

    side: async (args, passphrase) => {
      try {
        return await window.api.run({ jobId: `side-${++jobSeq}`, args, passphrase })
      } catch (err) {
        return failure(err)
      }
    },

    // a field changed: the window shows it at once, the project's settings file gets it when typing pauses
    set: (key, value) => {
      set({ settings: { ...get().settings, [key]: value } })
      if (key === 'close_to_tray') window.api.setCloseToTray(Boolean(value))
      if (key === 'minimize_to_tray') window.api.setMinimizeToTray(Boolean(value))
      if (key === 'start_with_system') void startWithSystem(Boolean(value), get().log)
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
        } else if (r.code === FORCE_STOPPED) {
          // while the stop holds, the status bar keeps saying so; the job's own line is in the console
          get().log(`${label}: ${r.message}`, 'warn')
          set({ status: get().stopped ? STOPPED_STATUS : `${label}: ${r.message}`, statusKind: 'err', progress: null })
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
        r = await window.api.run({ jobId, args: check ? ['update', `--project=${file}`, '--check', '--files'] : ['update', `--project=${file}`, '--files'] })
      } catch (err) {
        r = failure(err)
      }
      handlers.delete(jobId)
      jobTitles.delete(jobId)
      if (r.ok) {
        putRun(file, { state: 'done', jobId: undefined, progress: undefined, result: r.data as UpdateResult })
      } else {
        get().log(`[${p.title}] ${r.message ?? 'failed'}`, r.code === FORCE_STOPPED ? 'warn' : 'err')
        putRun(file, { state: 'failed', jobId: undefined, progress: undefined, message: r.message, stopped: r.code === 'stopped' || r.code === FORCE_STOPPED })
      }
    },

    // every project with a folder to update, several at the same time, in one engine job; each one's
    // progress and result come tagged with its file
    runAll: async (check, auto = false) => {
      if (get().allJob) return
      const ended: Ended[] = []
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
          ended.push({ file: e.file, ok: e.ok, result: e.data as UpdateResult | undefined, message: e.message })
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
        r = await window.api.run({ jobId, args: check ? ['update', '--all', '--check', '--files'] : ['update', '--all', '--files'] })
      } catch (err) {
        r = failure(err)
      }
      handlers.delete(jobId)
      // a project the run never finished (the engine itself stopped): failed, with the run's reason
      const runs = { ...get().runs }
      for (const [file, run] of Object.entries(runs)) {
        if (run.inAll) {
          runs[file] = {
            ...run, inAll: false, state: 'failed', progress: undefined, message: r.ok ? 'Not finished.' : r.message,
            stopped: r.code === 'stopped' || r.code === FORCE_STOPPED
          }
        }
      }
      const total = get().allJob?.total ?? 0
      set({ runs, allJob: null })
      if (!r.ok) get().log(r.code === FORCE_STOPPED ? `${label}: ${r.message}` : r.message ?? 'failed', r.code === FORCE_STOPPED ? 'warn' : 'err')
      if (!get().busy) {
        if (r.ok) set({ status: `${label} done.`, statusKind: 'ok', progress: { done: total, total, label: 'projects' } })
        else if (r.code === FORCE_STOPPED && get().stopped) set({ status: STOPPED_STATUS, statusKind: 'err', progress: null })
        else set({ status: r.code === 'stopped' ? `${label} stopped.` : `${label}: ${r.message}`, statusKind: 'err', progress: null })
      }
      // a check that Force stop ended updates nothing by itself and says nothing more
      if (auto && !get().stopped) await afterAutomatic(check, ended)
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
    },

    // The panic button: the window greys out at once, the dialogs close, and the main process ends every run
    // (each one then says it was stopped, the seed run too) and starts none until the stop is lifted. Seeding is
    // turned off for every project that had it.
    forceStop: async () => {
      useModal.getState().block(true)
      set({ stopped: true, importing: null, exporting: null, changesView: null })
      const r = await window.api.forceStop(true)
      set({ stoppedSince: r.since, status: STOPPED_STATUS, statusKind: 'err', progress: null })
      const what = r.stopped ? `${r.stopped} running ${r.stopped === 1 ? 'job' : 'jobs'} stopped` : 'nothing was running'
      get().log(`== Force stop: ${what}. Nothing runs until the stop is lifted.`, 'warn')
      if (r.problem) get().log(`Force stop holds until DeltaPatcher closes, not after a restart: ${r.problem}`, 'err')
      await turnSeedingOff()
    },

    // Lifted: nothing that was stopped starts again, and the automatic checks and backups count from now
    liftStop: async () => {
      const r = await window.api.forceStop(false)
      const now = Date.now()
      restartSchedules(now)
      restartBackups(get().projects, now)
      useModal.getState().block(false)
      set({ stopped: false, stoppedSince: '', seedTurnedOff: [], status: 'Force stop lifted. Nothing that was stopped starts again by itself.', statusKind: 'idle' })
      get().log('== Force stop lifted', 'head')
      if (r.problem) get().log(`Force stop comes back at the next start: ${r.problem}`, 'err')
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

// one project's end in Update all or Check all
interface Ended {
  file: string
  ok: boolean
  result?: UpdateResult
  message?: string
}

export function hasUpdate(r: ProjectRun | undefined): boolean {
  return Boolean(r && r.state === 'done' && r.check && r.result && !r.result.empty)
}

// how many projects the last checks found an update for: the count beside Projects
export function waitingCount(s: { projects: ProjectInfo[]; runs: Record<string, ProjectRun> }): number {
  return s.projects.filter((p) => hasUpdate(s.runs[p.file])).length
}

// names in a sentence: A; A and B; A, B and C; A, B, C and 2 more
export function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join('')
  const shown = names.length > 4 ? [...names.slice(0, 3), `${names.length - 3} more`] : names
  return `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`
}

// Every project checked by DeltaPatcher itself (at the start, then every check_every_hours hours), never while
// another run goes: 'busy' then, and the check waits; 'nothing' when no project has a folder and a source.
export function autoCheck(): 'started' | 'busy' | 'nothing' {
  const st = useApp.getState()
  if (st.stopped || anotherRun(st)) return 'busy'
  if (!st.projects.some((p) => p.update_ready)) return 'nothing'
  useApp.setState({ autoChecked: true })
  void st.runAll(true, true)
  return 'started'
}

// when the app last started a backup of each project by itself (milliseconds); the projects whose last one is
// tried again sooner (it failed, or another backup of it was running); those whose last one failed
const backupTried = new Map<string, number>()
const backupRetry = new Set<string>()
const backupFailing = new Set<string>()

// when the app backs a project up by itself next; null: never
function nextAutoBackup(p: ProjectInfo): number | null {
  return nextBackup(p.backup_every_hours, madeAt(p.backup_last), backupTried.get(p.file) ?? null, backupRetry.has(p.file))
}

// Force stop lifted: each project's next backup by itself comes backup_every_hours from now
function restartBackups(projects: ProjectInfo[], now: number): void {
  for (const p of projects) backupTried.set(p.file, now)
  backupRetry.clear()
}

// what a backup of a project did: a failure stays on its Backups card (and in the status bar) until one works
export function noteBackup(file: string, failed: string | null): void {
  const problems = { ...useApp.getState().backupProblems }
  if (failed === null) delete problems[file]
  else problems[file] = failed
  useApp.setState({ backupProblems: problems })
}

// A backup the app makes by itself (backup_every_hours), one project at a time and never while another run goes
// or Force stop is on. Said in the console; a failure also in the status bar and on the project's Backups card,
// and the first failure in a row in a notification (shown while the window is not on screen). A failure, or
// another backup of the project running (the system's scheduled one), is tried again 15 minutes later; a run
// Force stop ended is neither.
export async function autoBackup(): Promise<void> {
  const st = useApp.getState()
  if (st.stopped || anotherRun(st)) return
  const at = Date.now()
  const p = st.projects.find((x) => {
    const next = !x.missing && x.backup_ready ? nextAutoBackup(x) : null
    return next !== null && at >= next
  })
  if (!p) return
  backupTried.set(p.file, at)
  const jobId = `bak-${++jobSeq}`
  jobTitles.set(jobId, p.title)
  useApp.setState({ backupJob: p.file })
  let r: EngineOutcome
  try {
    r = await window.api.run({ jobId, args: ['backup', 'now', `--project=${p.file}`] })
  } catch (err) {
    r = failure(err)
  }
  jobTitles.delete(jobId)
  useApp.setState({ backupJob: null })
  const out = autoOutcome(r, backupFailing.has(p.file), r.code === FORCE_STOPPED)
  st.log(`[${p.title}] ${out.text}`, out.style)
  if (out.retry) backupRetry.add(p.file)
  else backupRetry.delete(p.file)
  if (out.failed) {
    backupFailing.add(p.file)
    noteBackup(p.file, r.message ?? 'failed')
    if (!useApp.getState().busy) useApp.setState({ status: `The backup of ${p.title} failed: ${r.message}`, statusKind: 'err' })
  } else if (r.ok) {
    backupFailing.delete(p.file)
    noteBackup(p.file, null)
  }
  if (out.notify) await window.api.notify('DeltaPatcher', `The backup of ${p.title} failed: ${r.message}`, true)
  await useApp.getState().loadProjects()
}

// What an automatic check found, acted on: with auto_update the projects with an update update by themselves,
// and a notification says what was done; otherwise, or while another run goes, a notification names them
// while the window is not on screen.
async function afterAutomatic(check: boolean, ended: Ended[]): Promise<void> {
  const st = useApp.getState()
  if (st.stopped) return
  const titleOf = (file: string): string => st.projects.find((p) => p.file === file)?.title ?? file
  if (check) {
    const waiting = ended.filter((e) => e.ok && e.result && !e.result.empty).map((e) => e.file)
    for (const f of [...notified]) if (!waiting.includes(f)) notified.delete(f)
    if (!waiting.length) return
    if (st.settings.auto_update === true && !anotherRun(useApp.getState())) {
      await st.runAll(false, true)
      return
    }
    if (waiting.every((f) => notified.has(f))) return
    waiting.forEach((f) => notified.add(f))
    const names = joinNames(waiting.map(titleOf))
    const text = waiting.length === 1 ? `${names} has an update.` : `${waiting.length} projects have an update: ${names}.`
    st.log(text, 'warn')
    await window.api.notify('DeltaPatcher', text, true)
    return
  }
  const updated = ended.filter((e) => e.ok && e.result && !e.result.empty).map((e) => titleOf(e.file))
  const failed = ended.filter((e) => !e.ok)
  ended.forEach((e) => notified.delete(e.file))
  const lines = updated.length ? [`Updated by itself: ${joinNames(updated)}.`] : []
  for (const e of failed) lines.push(`${titleOf(e.file)} failed: ${e.message ?? 'see the console'}`)
  if (!lines.length) return
  st.log(lines.join('\n'), failed.length ? 'warn' : 'ok')
  await window.api.notify('DeltaPatcher', lines.join('\n'), false)
}

// ---- seeding (store/seeding.ts)

// Outside the store: the window stopped the seed run to start it again; how many runs in a row ended by themselves;
// the timers of a start again; why seeding does not run, as last said in the console; the tray's count.
let seedRestarting = false
let seedRetries = 0
let seedRetryTimer: ReturnType<typeof setTimeout> | null = null
let seedRestartTimer: ReturnType<typeof setTimeout> | null = null
let seedSaid = ''
let trayCount = 0

function clearSeedTimers(): void {
  if (seedRetryTimer) clearTimeout(seedRetryTimer)
  if (seedRestartTimer) clearTimeout(seedRestartTimer)
  seedRetryTimer = null
  seedRestartTimer = null
}

// why seeding does not run: on the rows of the projects set to seed, and in the console once
function seedSay(why: string): void {
  useApp.setState({ seedWhy: why })
  if (why && why !== seedSaid) useApp.getState().log(why, 'warn')
  seedSaid = why
}

// the tray's tooltip: how many projects are seeded now
function seedCount(rows: Record<string, SeedRow>): void {
  const n = seedingCount(rows)
  if (n === trayCount) return
  trayCount = n
  void window.api.setSeeding(n)
}

// Seeding starts when it is wanted and no run goes: seed --all --json, one engine run beside everything else that
// never makes the window busy (as the local server). Its "seeding" events are each project's row. It reads the
// settings again by itself: seed = no drops a project, seed = yes adds one, refused torrents end it. Once it has
// ended, afterRun() says what comes next.
export async function startSeeding(): Promise<void> {
  const st = useApp.getState()
  if (st.seeding || !wantsSeeding(st.projects, st.effective('torrents'), st.stopped)) return
  clearSeedTimers()
  // unique beyond this window: a run that a window before it started (on macOS the window can close and open again
  // while DeltaPatcher runs) keeps an id of its own, so Force stop still reaches it
  const jobId = `seed-${Date.now().toString(36)}-${++jobSeq}`
  const since = Date.now()
  handlers.set(jobId, (e) => {
    if (e.event !== 'seeding') return
    const rows = withRow(useApp.getState().seedRows, e, Date.now())
    useApp.setState({ seedRows: rows })
    seedCount(rows)
  })
  seedSaid = ''
  useApp.setState({ seeding: { jobId, since }, seedWhy: '' })
  let r: EngineOutcome
  try {
    r = await window.api.run({ jobId, args: ['seed', '--all'] })
  } catch (err) {
    r = failure(err)
  }
  handlers.delete(jobId)
  useApp.setState({ seeding: null, seedRows: {} })
  seedCount({})
  const restarting = seedRestarting
  seedRestarting = false
  // the seed flags as they are now: the run may have ended because of them
  if (!useApp.getState().stopped && r.code !== FORCE_STOPPED) await useApp.getState().loadProjects()
  const now = useApp.getState()
  const next = afterRun(r, {
    restarting,
    stopped: now.stopped,
    wanted: wantsSeeding(now.projects, now.effective('torrents'), now.stopped),
    refused: torrentsRefused(now.effective('torrents')),
    ranFor: Date.now() - since,
    retries: seedRetries
  })
  if (next.next === 'start') {
    void startSeeding()
  } else if (next.next === 'wait') {
    seedRetries = 0
    seedSay(next.why)
  } else if (next.next === 'retry') {
    seedRetries = next.retries
    seedSay(next.why)
    seedRetryTimer = setTimeout(() => {
      seedRetryTimer = null
      void startSeeding()
    }, next.after)
  }
}

// The seed run starts again, a moment after the last call: a port typed digit by digit starts it again once.
function restartSeeding(why: string, delay = 3000): void {
  if (seedRestartTimer) clearTimeout(seedRestartTimer)
  seedRestartTimer = setTimeout(() => {
    seedRestartTimer = null
    const s = useApp.getState().seeding
    if (!s || useApp.getState().stopped) return
    seedRestarting = true
    useApp.getState().log(why)
    void window.api.stop(s.jobId)
  }, delay)
}

// A setting of seeding was saved (the Settings page, a row's menu, the import dialog, the Release page). A run
// picks up seed and torrents by itself; one that reads a setting only as it starts (the port, the upload limit, port
// mapping, and where the shown project's files are when it is set to seed) starts again a moment after the last
// change. With no run going, one starts when it is wanted now.
export async function seedSettingsChanged(keys: string[]): Promise<void> {
  const seedKey = keys.some((k) => SEED_KEYS.includes(k))
  const placeKey = useApp.getState().settings.seed === true && keys.some((k) => PLACE_KEYS.includes(k))
  const restartKey = placeKey || keys.some((k) => RESTART_KEYS.includes(k))
  if (!seedKey && !restartKey) return
  seedRetries = 0
  const st = useApp.getState()
  if (st.seeding) {
    if (restartKey) restartSeeding('Seeding starts again with the new settings.')
    return
  }
  if (seedRetryTimer) clearTimeout(seedRetryTimer)
  seedRetryTimer = null
  seedSaid = ''
  useApp.setState({ seedWhy: '' })
  await st.loadProjects()
  void startSeeding()
}

// A release made the torrent of the shown project, set to seed: a run seeds a store only once the store has a
// torrent, so one that began before seeds it once it starts again; with no run going, one starts.
export function seedAfterPublish(file: string): void {
  const st = useApp.getState()
  if (st.project !== file || st.settings.seed !== true || st.stopped) return
  if (!st.seeding) {
    void seedSettingsChanged(['seed'])
    return
  }
  if (st.seedRows[file]?.kind !== 'store') restartSeeding('Seeding starts again, to seed the new release from its store.', 0)
}

// Force stop turns seeding off: seed = no for every project set to seed (config set runs while the stop holds), so
// nothing seeds once the stop is lifted until the person turns it on again. A seed = yes typed a moment before is
// not saved after it.
async function turnSeedingOff(): Promise<void> {
  clearSeedTimers()
  delete pending.seed
  const st = useApp.getState()
  if (st.settings.seed === true) useApp.setState({ settings: { ...useApp.getState().settings, seed: false } })
  const list = await st.side(['projects'])
  const projects = (list.ok ? list.data.projects ?? [] : st.projects) as ProjectInfo[]
  const off: string[] = []
  for (const args of seedOffArgs(projects)) {
    const p = projects.find((x) => args[3] === `--project=${x.file}`)
    const r = await st.side(args)
    if (r.ok) off.push(p?.title ?? args[3])
    else st.log(`Seeding could not be turned off for ${p?.title ?? args[3]}: ${r.message}`, 'err')
  }
  if (off.length) {
    useApp.setState({ seedTurnedOff: off })
    st.log(`Seeding was turned off for ${joinNames(off)}: it stays off until you turn it on again.`, 'warn')
  }
  await useApp.getState().loadProjects()
}
