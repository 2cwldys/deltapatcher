// What the project picker, the Projects page and the Settings page do with projects, with the same questions
// everywhere: make, rename and remove them; add a project file (imported, or updating the project it is for; a
// publisher's private file sets up releasing) or a settings file (listed); export a project file, public or
// private; apply a publisher's change or answer it Not now; show what a release changed; seed a project or stop
// seeding it; remove every project; reset DeltaPatcher.
import { Exporting, ImportEntry, ProjectInfo, seedSettingsChanged, UpdateResult, useApp } from './app'
import { ask } from './modal'
import { nameRules } from './folders'
import { acceptArgs, applyQuestion, inPlace, PublisherChange, updatesDefault } from './publisher'
import {
  exportChosen, exportName, PRIVATE_EXPORT, privateExportArgs, privateImportArgs, privateRuns, seedAfterSetUp, setUpNotes
} from './privateFiles'

const PROJECT_FILE = /\.(deltapatch|html?)$/i

// The files Add a project file takes, in plain words.
export const WHICH_FILES =
  'A project file: a .deltapatch or .html file from a publisher.\nOr, advanced: a project\'s .ini settings file.'

// what is wrong with a project's name ('' when nothing): the engine's own rules, said while it is typed
export function nameProblem(name: string, taken: string[]): string {
  const n = name.trim()
  if (!n) return 'Give it a name.'
  const rules = nameRules(n)
  if (rules) return rules
  if (taken.some((t) => t.toLowerCase() === n.toLowerCase())) return 'Another project has that name.'
  return ''
}

// a text for the window with each sentence on its own line
export function sentences(text: string): string {
  return text.replace(/([.!?])\s+(?=[A-Z(]|[a-z]+:)/g, '$1\n')
}

function baseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

// a name that is free: the name itself, else the name with 2, 3...
function freeName(name: string, taken: string[]): string {
  const lower = taken.map((t) => t.toLowerCase())
  if (!lower.includes(name.toLowerCase())) return name
  for (let i = 2; ; i++) {
    const n = `${name.slice(0, 56)} ${i}`
    if (!lower.includes(n.toLowerCase())) return n
  }
}

async function failed(title: string, message: string | undefined): Promise<void> {
  await ask.info(title, message ?? 'The engine gave no reason.')
}

// a new project in DeltaPatcher's projects folder, shown at once
export async function newProject(): Promise<void> {
  const st = useApp.getState()
  const taken = st.projects.map((p) => p.title)
  const name = await ask.prompt(
    'New project',
    'A game, a mod or a server of its own: its own folder to keep up to date, its own release.\n\n' +
      'Its name (letters, digits, spaces and - _ . ( ), up to 60 characters):',
    '',
    'Make it',
    (v) => nameProblem(v, taken)
  )
  if (!name) return
  const r = await st.side(['project', 'new', name])
  if (!r.ok) return failed('No project made', r.message)
  await st.loadProjects()
  await useApp.getState().selectProject(String(r.data.file))
}

export async function renameProject(p: ProjectInfo): Promise<void> {
  const st = useApp.getState()
  const taken = st.projects.filter((x) => x.file !== p.file).map((x) => x.title)
  const name = await ask.prompt(
    'Rename the project',
    `Its name in DeltaPatcher's lists.\nIts settings file stays where it is:\n${p.file}`,
    p.title,
    'Rename',
    (v) => nameProblem(v, taken)
  )
  if (!name || name === p.title) return
  const r = await st.side(['project', 'rename', p.file, name])
  if (!r.ok) return failed('Not renamed', r.message)
  await st.loadProjects()
}

// Remove one project, after the same question everywhere. True when it was removed.
export async function removeProject(p: ProjectInfo): Promise<boolean> {
  const body =
    p.kind === 'folder'
      ? `Its settings file is deleted:\n${p.file}\n\nThe folders it updates or releases are not touched.`
      : `It only leaves the list.\nIts settings file stays where it is:\n${p.file}\n\nThe folders it updates or releases are not touched.`
  if (!(await ask.confirm(`Remove ${p.title}?`, body, 'Remove', true))) return false
  const st = useApp.getState()
  const r = await st.side(['project', 'remove', p.file, '--yes'])
  if (!r.ok) {
    await failed('Not removed', r.message)
    return false
  }
  if (st.project === p.file && st.projects[0]) await st.selectProject(st.projects[0].file)
  await useApp.getState().loadProjects()
  return true
}

// Remove every project but Default, after one question that names them.
export async function removeAll(): Promise<void> {
  const st = useApp.getState()
  const others = st.projects.slice(1)
  if (!others.length) return
  const list = others.map((p) => `${p.title}: ${p.kind === 'folder' ? 'its settings file is deleted' : 'it only leaves the list'}`)
  const body = `${list.join('\n')}\n\nDefault stays.\nThe folders they update or release are not touched.`
  if (!(await ask.confirm(`Remove ${others.length === 1 ? 'the project' : `all ${others.length} projects`} but Default?`, body, 'Remove them', true))) return
  const r = await st.side(['project', 'remove', '--all', '--yes'])
  if (!r.ok) return failed('Not removed', r.message)
  if (st.project !== st.projects[0].file) await st.selectProject(st.projects[0].file)
  await useApp.getState().loadProjects()
}

// Add a project file...: the file dialog, then the file by its type
export async function openProjectFile(): Promise<void> {
  const path = await window.api.pickProjectFile('')
  if (path) await addFile(path)
}

// a file picked, dropped onto the window or double-clicked: a project file is imported, a settings file listed
export async function addFile(path: string): Promise<void> {
  const st = useApp.getState()
  if (st.stopped) {
    st.log(`Not added while Force stop is on: ${path}. Lift the stop, then add it again.`, 'warn')
    return
  }
  if (PROJECT_FILE.test(path)) return startImport(path)
  if (/\.ini$/i.test(path)) return addSettingsFile(path)
  await ask.info('Not a project file', `${path}\n\nDeltaPatcher adds these files:\n${WHICH_FILES}`)
}

// a project's .ini settings file kept elsewhere (a game's folder, a share): listed, never moved
async function addSettingsFile(file: string): Promise<void> {
  const st = useApp.getState()
  let r = await st.side(['project', 'add', file])
  if (!r.ok && r.code === 'name-taken') {
    const taken = st.projects.map((p) => p.title)
    const name = await ask.prompt(
      'Give it another name',
      `There is a project named ${String(r.data.name)} already.\nA name for the one in\n${file}\n\nIt is written into that file.`,
      freeName(String(r.data.name), taken),
      'Add it',
      (v) => nameProblem(v, taken)
    )
    if (!name) return
    r = await st.side(['project', 'add', file, `--name=${name}`])
  }
  if (!r.ok) return failed('Not added', r.message)
  await st.loadProjects()
  await useApp.getState().selectProject(String(r.data.file))
}

// a project file: read and checked by the engine, then the import dialog. A publisher's private file opens on
// setting up releasing; asPlayer: it is added as a player instead, with only what a public file holds
export async function startImport(path: string, asPlayer = false): Promise<void> {
  const st = useApp.getState()
  const r = await st.side(['project', 'import', path, '--dry-run', ...(asPlayer ? ['--as-player'] : [])])
  if (!r.ok) {
    const why =
      r.code === 'tampered'
        ? 'It was changed after it was signed, so it is refused.\nAsk its publisher for the file again.'
        : r.code === 'newer'
          ? 'It was made by a newer DeltaPatcher.\nUpdate DeltaPatcher first.'
          : sentences(String(r.message ?? 'The engine gave no reason.'))
    return ask.info('This project file cannot be added', `${path}\n\n${why}`)
  }
  // Force stop came while the file was read: no dialog opens
  if (useApp.getState().stopped) return
  const taken = st.projects.map((p) => p.title)
  // this computer's keys, as a project gets them unless the person picks others
  const fallback = (key: string): string => String(st.schema.find((x) => x.key === key)?.default ?? '')
  const entries: ImportEntry[] = (r.data.projects ?? []).map((e: ImportEntry) => ({
    ...e,
    pick: true,
    name: e.taken ? freeName(e.title, taken) : e.title,
    folder: '',
    asNew: false,
    seed: false,
    publishSrc: '',
    signingKey: fallback('signing_key'),
    sshKey: fallback('ssh_key'),
    deployFolder: '',
    trustHostKeys: false,
    applyOlder: false
  }))
  useApp.setState({
    importing: {
      file: path,
      signed: Boolean(r.data.signed),
      signer: String(r.data.signer ?? ''),
      fingerprint: String(r.data.fingerprint ?? ''),
      updates: Boolean(r.data.updates),
      torrentsRefused: Boolean(r.data.torrents_refused),
      entries,
      updateNow: true,
      working: false,
      private: Boolean(r.data.private),
      asPlayer
    }
  })
}

// Set up releasing in the import dialog of a private file: each ticked project with this computer's folders and
// keys (or the project here it updates, which keeps its own; from an older file only after Apply it anyway), the
// host keys only when the person said so; then the first project set up is shown on the Release page, with what it
// still needs
async function finishPrivateImport(): Promise<void> {
  const st = useApp.getState()
  const im = st.importing
  if (!im) return
  useApp.setState({ importing: { ...im, working: true } })
  const added: { project: string; file: string; key_check?: string; host_keys?: string; publish_src?: string; seed?: boolean }[] = []
  const updated: string[] = []
  const problems: string[] = []
  for (const e of privateRuns(im.entries)) {
    const r = await st.side(privateImportArgs(im.file, e, im.torrentsRefused))
    if (r.ok) {
      for (const a of r.data.added ?? []) added.push(a)
      for (const u of r.data.updated ?? []) updated.push(String(u.file))
    } else {
      problems.push(`${e.title}: ${r.message}`)
    }
  }
  useApp.setState({ importing: null })
  await st.loadProjects()
  if (seedAfterSetUp(added)) void seedSettingsChanged(['seed'])
  const first = added[0]?.file ?? updated[0]
  if (first) {
    await useApp.getState().selectProject(first)
    useApp.getState().setPage('release')
  }
  if (problems.length) await ask.info('Not every project was set up', problems.join('\n'))
  const notes = setUpNotes(added)
  if (notes.length) await ask.info('Set up for releasing', notes.join('\n'))
}

// Add (or Update) in the import dialog: each ticked project with its name and folder (and seeding, when the person
// said yes to it), or the project here it updates, then the updates asked for
export async function finishImport(): Promise<void> {
  const st = useApp.getState()
  const im = st.importing
  if (!im) return
  if (im.private && !im.asPlayer) return finishPrivateImport()
  useApp.setState({ importing: { ...im, working: true } })
  const added: { file: string; folder: string; seed: boolean }[] = []
  const updated: string[] = []
  const problems: string[] = []
  for (const e of im.entries.filter((x) => x.pick)) {
    const args = ['project', 'import', im.file, `--only=${e.title}`, '--yes', ...(im.asPlayer ? ['--as-player'] : [])]
    if (!inPlace(e)) {
      if (e.updates) args.push('--as-new')
      if (e.name.trim() !== e.title) args.push(`--name=${e.name.trim()}`)
      // the folder as the dialog shows it: the engine adds no install_folder to it
      if (e.folder.trim()) args.push(`--folder=${e.folder.trim()}`, '--as-is')
      if (e.seed && e.torrent && !im.torrentsRefused) args.push('--seed')
    }
    const r = await st.side(args)
    if (r.ok) {
      for (const a of r.data.added ?? []) added.push({ file: String(a.file), folder: String(a.install_dir ?? ''), seed: Boolean(a.seed) })
      for (const u of r.data.updated ?? []) updated.push(String(u.file))
    } else {
      problems.push(`${e.title}: ${r.message}`)
    }
  }
  useApp.setState({ importing: null })
  await st.loadProjects()
  // a project added to seed: the seed run takes it, or starts
  if (added.some((a) => a.seed)) void seedSettingsChanged(['seed'])
  if (added.length) await useApp.getState().selectProject(added[0].file)
  else if (updated.length) await useApp.getState().selectProject(updated[0])
  for (const file of updated) useApp.getState().settlePrompt(file)
  if (problems.length) await ask.info('Not every project was added', problems.join('\n'))
  const toUpdate = im.updateNow && !useApp.getState().stopped ? added.filter((a) => a.folder) : []
  if (toUpdate.length) {
    useApp.getState().setPage('projects')
    for (const a of toUpdate) void useApp.getState().runProject(a.file, false)
  }
}

// Export...: the dialog, with the picked project ticked and the signing key looked at
export async function startExport(file?: string): Promise<void> {
  const st = useApp.getState()
  const key = String(st.effective('signing_key'))
  const status = await st.side(['sign', 'status', `--path=${key}`])
  const exists = Boolean(status.ok && status.data.exists && status.data.can_sign)
  if (useApp.getState().stopped) return
  const picked = file ?? st.project
  useApp.setState({
    exporting: {
      type: 'public',
      picks: { [picked]: true },
      kind: 'deltapatch',
      updates: updatesDefault(st.projects.filter((p) => p.file === picked)),
      sign: exists,
      key,
      keyExists: exists,
      keyLocked: Boolean(status.ok && status.data.has_passphrase)
    }
  })
}

// Export of a private file: always signed with the projects' own signing key (its passphrase asked when it has
// one, never kept), saved as "<project> (private)", then what it holds and that the key moves by hand
async function finishPrivateExport(chosen: ProjectInfo[], kind: Exporting['kind']): Promise<void> {
  const st = useApp.getState()
  const cfg = await st.side(['config', 'get', `--project=${chosen[0].file}`])
  if (!cfg.ok) return failed('The private project file was not saved', cfg.message)
  const key = String(cfg.data.settings?.signing_key ?? '')
  const status = await st.side(['sign', 'status', `--path=${key}`])
  if (!status.ok || !status.data.exists || !status.data.can_sign) {
    return ask.info('The private project file was not saved', `There is no signing key at\n${key}\n\nA private project file is signed with its projects' signing key.`)
  }
  let passphrase: string | undefined
  if (status.data.has_passphrase) {
    const p = await ask.passphrase('Sign the private project file', `The passphrase of the signing key\n${key}`)
    if (p === null) return
    passphrase = p
  }
  const out = await window.api.saveProjectFile(exportName(chosen.map((p) => p.title), 'private'), kind)
  if (!out) return
  const r = await st.side(privateExportArgs(chosen.map((p) => p.file), out, passphrase !== undefined), passphrase)
  if (!r.ok) return failed('The private project file was not saved', sentences(String(r.message ?? '')))
  const show = await ask.confirm(
    'Private project file saved',
    `${out}\n\n${chosen.length === 1 ? 'One project' : `${chosen.length} projects`}: ${chosen.map((p) => p.title).join(', ')}.\n` +
      `It is signed with your signing key.\n${PRIVATE_EXPORT}\nOn your other computer, open it with DeltaPatcher.`,
    'Show the file'
  )
  if (show) await window.api.show(out)
}

// Export in the dialog: the passphrase when it is signed (asked, never kept), where to save it, then the engine
// writes it
export async function finishExport(): Promise<void> {
  const st = useApp.getState()
  const ex = st.exporting
  if (!ex) return
  const chosen = exportChosen(st.projects, ex.picks, ex.type)
  if (!chosen.length) return
  useApp.setState({ exporting: null })
  if (ex.type === 'private') return finishPrivateExport(chosen, ex.kind)
  const signing = ex.sign && ex.keyExists
  let passphrase: string | undefined
  if (signing && ex.keyLocked) {
    const p = await ask.passphrase('Sign the project file', `The passphrase of the signing key\n${ex.key}`)
    if (p === null) return
    passphrase = p
  }
  const out = await window.api.saveProjectFile(chosen.length === 1 ? chosen[0].title : 'Projects', ex.kind)
  if (!out) return
  const args = ['project', 'export', ...chosen.map((p) => p.file), `--out=${out}`, ex.updates ? '--updates' : '--no-updates']
  if (signing) args.push(`--sign=${ex.key}`)
  if (passphrase !== undefined) args.push('--passphrase-stdin')
  const r = await st.side(args, passphrase)
  if (!r.ok) return failed('The project file was not saved', sentences(String(r.message ?? '')))
  const signed = r.data.signed ? 'It is signed with your signing key.' : 'It is not signed: whoever adds it is told so.'
  const kind = r.data.updates
    ? 'It is update capable: players are asked when you change these projects later.'
    : 'Each player who opens it gets a project of their own.'
  const show = await ask.confirm(
    'Project file saved',
    `${out}\n\n${chosen.length === 1 ? 'One project' : `${chosen.length} projects`}: ${chosen.map((p) => p.title).join(', ')}.\n` +
      `${signed}\n${kind}\nPlayers drag it onto DeltaPatcher's window, or double-click a .deltapatch file.`,
    'Show the file'
  )
  if (show) await window.api.show(out)
}

// Apply on a publisher's change: what changes and the warning a new address or key comes with, then only the
// change that was shown is applied (the engine refuses one the publisher made since)
export async function applyPublisher(file: string, change: PublisherChange): Promise<void> {
  const st = useApp.getState()
  const title = st.projects.find((p) => p.file === file)?.title ?? 'this project'
  const q = applyQuestion(title, change)
  if (!(await ask.confirm(q.title, q.body, 'Apply', q.danger))) return
  const r = await st.side(acceptArgs(file, change, false))
  if (!r.ok) {
    if (r.code === 'changed-since') {
      useApp.getState().settlePrompt(file)
      return ask.info('The publisher changed it again', `${title} changed again since this was shown.\nCheck it again to see what is waiting.`)
    }
    return failed('Not applied', sentences(String(r.message ?? '')))
  }
  const words = Object.keys(r.data.applied ?? {}).length ? change.words : 'nothing'
  st.log(`[${title}] Applied the publisher's change: ${words}.`, 'ok')
  useApp.getState().settlePrompt(file)
  await useApp.getState().loadProjects()
  // the shown project's settings are what was applied
  if (useApp.getState().project === file) {
    const cfg = await st.side(['config', 'get', `--project=${file}`])
    if (cfg.ok && useApp.getState().project === file) useApp.setState({ settings: cfg.data.settings ?? {} })
  }
}

// Not now: the change is not asked again until the publisher changes the project again
export async function notNowPublisher(file: string, change: PublisherChange): Promise<void> {
  const st = useApp.getState()
  const r = await st.side(acceptArgs(file, change, true))
  if (!r.ok && r.code !== 'changed-since') return failed('Not saved', sentences(String(r.message ?? '')))
  useApp.getState().settlePrompt(file)
}

// Seed this project, or Stop seeding (its row's menu): seed = yes or no in its settings. A seed run picks that up
// within seconds; with none going, one starts.
export async function setSeed(p: ProjectInfo, on: boolean): Promise<void> {
  const st = useApp.getState()
  const r = await st.side(['config', 'set', `seed=${on ? 'yes' : 'no'}`, `--project=${p.file}`])
  if (!r.ok) return failed(on ? 'Not set to seed' : 'Seeding not stopped', sentences(String(r.message ?? '')))
  const now = useApp.getState()
  if (now.project === p.file) useApp.setState({ settings: { ...now.settings, seed: on } })
  if (!on && now.seedRows[p.file]) {
    const rows = { ...now.seedRows }
    delete rows[p.file]
    useApp.setState({ seedRows: rows })
  }
  await seedSettingsChanged(['seed'])
}

// What changed: the dialog with the release's notes, its files by name and its whole list
export function showChanges(title: string, result: UpdateResult): void {
  if (useApp.getState().stopped) return
  useApp.setState({ changesView: { title, result } })
}

// Reset DeltaPatcher: the engine says what it does, the person agrees, then the window starts afresh
export async function resetAll(): Promise<void> {
  const st = useApp.getState()
  const plan = await st.side(['reset'])
  if (plan.ok || plan.code !== 'confirm-needed') return failed('DeltaPatcher was not reset', plan.message)
  const d = plan.data
  const removed = ((d.removed ?? []) as { project: string }[]).map((p) => p.project)
  const body = [
    'DeltaPatcher goes back to how it was just installed.',
    '',
    removed.length ? `Removed: every project but Default (${removed.join(', ')}).` : 'Removed: no project, Default is the only one.',
    'A file in the projects folder is deleted; an added one only leaves the list.',
    `Back to its defaults: the settings file ${String(d.settings)}`,
    "Cleared: the window's saved state and the cached check for a newer DeltaPatcher.",
    '',
    'Never touched: the folders projects update or release and their files, the signing key, SSH keys.',
    'Kept: trusted server host keys, the log, prepared releases.',
    '',
    `A copy of the settings files as they are goes to:\n${String(d.copy)}`
  ].join('\n')
  if (!(await ask.confirm('Reset DeltaPatcher?', body, 'Reset DeltaPatcher', true))) return
  const r = await st.side(['reset', '--yes'])
  if (!r.ok) return failed('DeltaPatcher was not reset', r.message)
  await ask.info('DeltaPatcher is reset', `It is as it was just installed.\nThe settings files as they were are in:\n${String(r.data.copy)}`)
  await window.api.startAfresh()
}

export { baseName }
