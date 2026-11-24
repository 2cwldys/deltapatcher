// What the project picker, the Projects page and the Settings page do with projects, with the same questions
// everywhere: make, rename and remove them; add a project file (imported) or a settings file (listed); export a
// project file; remove every project; reset DeltaPatcher.
import { ImportEntry, ProjectInfo, useApp } from './app'
import { ask } from './modal'
import { nameRules } from './folders'

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

// a project file: read and checked by the engine, then the import dialog
export async function startImport(path: string): Promise<void> {
  const st = useApp.getState()
  const r = await st.side(['project', 'import', path, '--dry-run'])
  if (!r.ok) {
    const why =
      r.code === 'tampered'
        ? 'It was changed after it was signed, so it is refused.\nAsk its publisher for the file again.'
        : r.code === 'newer'
          ? 'It was made by a newer DeltaPatcher.\nUpdate DeltaPatcher first.'
          : sentences(String(r.message ?? 'The engine gave no reason.'))
    return ask.info('This project file cannot be added', `${path}\n\n${why}`)
  }
  const taken = st.projects.map((p) => p.title)
  const entries: ImportEntry[] = (r.data.projects ?? []).map((e: ImportEntry) => ({
    ...e,
    pick: true,
    name: e.taken ? freeName(e.title, taken) : e.title,
    folder: ''
  }))
  useApp.setState({
    importing: {
      file: path,
      signed: Boolean(r.data.signed),
      signer: String(r.data.signer ?? ''),
      fingerprint: String(r.data.fingerprint ?? ''),
      entries,
      updateNow: true,
      working: false
    }
  })
}

// Add in the import dialog: each ticked project with its name and folder, then the updates asked for
export async function finishImport(): Promise<void> {
  const st = useApp.getState()
  const im = st.importing
  if (!im) return
  useApp.setState({ importing: { ...im, working: true } })
  const added: { file: string; folder: string }[] = []
  const problems: string[] = []
  for (const e of im.entries.filter((x) => x.pick)) {
    const args = ['project', 'import', im.file, `--only=${e.title}`, '--yes']
    if (e.name.trim() !== e.title) args.push(`--name=${e.name.trim()}`)
    // the folder as the dialog shows it: the engine adds no install_folder to it
    if (e.folder.trim()) args.push(`--folder=${e.folder.trim()}`, '--as-is')
    const r = await st.side(args)
    if (r.ok) {
      for (const a of r.data.added ?? []) added.push({ file: String(a.file), folder: String(a.install_dir ?? '') })
    } else {
      problems.push(`${e.title}: ${r.message}`)
    }
  }
  useApp.setState({ importing: null })
  await st.loadProjects()
  if (added.length) await useApp.getState().selectProject(added[0].file)
  if (problems.length) await ask.info('Not every project was added', problems.join('\n'))
  const toUpdate = im.updateNow ? added.filter((a) => a.folder) : []
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
  useApp.setState({
    exporting: {
      picks: { [file ?? st.project]: true },
      kind: 'deltapatch',
      sign: exists,
      key,
      keyExists: exists,
      keyLocked: Boolean(status.ok && status.data.has_passphrase)
    }
  })
}

// Export in the dialog: the passphrase when it is signed (asked, never kept), where to save it, then the engine
// writes it
export async function finishExport(): Promise<void> {
  const st = useApp.getState()
  const ex = st.exporting
  if (!ex) return
  const chosen = st.projects.filter((p) => ex.picks[p.file])
  if (!chosen.length) return
  useApp.setState({ exporting: null })
  const signing = ex.sign && ex.keyExists
  let passphrase: string | undefined
  if (signing && ex.keyLocked) {
    const p = await ask.passphrase('Sign the project file', `The passphrase of the signing key\n${ex.key}`)
    if (p === null) return
    passphrase = p
  }
  const out = await window.api.saveProjectFile(chosen.length === 1 ? chosen[0].title : 'Projects', ex.kind)
  if (!out) return
  const args = ['project', 'export', ...chosen.map((p) => p.file), `--out=${out}`]
  if (signing) args.push(`--sign=${ex.key}`)
  if (passphrase !== undefined) args.push('--passphrase-stdin')
  const r = await st.side(args, passphrase)
  if (!r.ok) return failed('The project file was not saved', sentences(String(r.message ?? '')))
  const signed = r.data.signed ? 'It is signed with your signing key.' : 'It is not signed: whoever adds it is told so.'
  const show = await ask.confirm(
    'Project file saved',
    `${out}\n\n${chosen.length === 1 ? 'One project' : `${chosen.length} projects`}: ${chosen.map((p) => p.title).join(', ')}.\n` +
      `${signed}\nPlayers drag it onto DeltaPatcher's window, or double-click a .deltapatch file.`,
    'Show the file'
  )
  if (show) await window.api.show(out)
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
