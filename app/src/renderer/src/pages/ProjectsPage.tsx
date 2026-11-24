// The Projects page: every project at a glance with its state. Check or update one, or all of them at once
// (several at the same time, each with its own progress); open one on the other pages; make, add, rename
// and remove projects.
import { ReactNode, useEffect } from 'react'
import {
  ArrowRight, Boxes, CircleAlert, CircleCheck, Download, FilePlus, FileSearch, FolderKanban, Pencil, Plus, Search,
  Square, Trash2
} from 'lucide-react'
import { ProjectInfo, ProjectRun, Progress, UpdateResult, running, useApp } from '../store/app'
import { ask } from '../store/modal'
import { Banner, Button, Card, Chip, human } from '../components/ui'
import { insecure } from './UpdatePage'

const RESERVED = new Set(['CON', 'PRN', 'AUX', 'NUL', ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((i) => [`COM${i}`, `LPT${i}`])])

// what is wrong with a project's name ('' when nothing): the engine's own rules, said while it is typed
export function nameProblem(name: string, taken: string[]): string {
  const n = name.trim()
  if (!n) return 'Give it a name.'
  if (n.length > 60) return 'At most 60 characters.'
  if (!/^[A-Za-z0-9 _.()-]+$/.test(n)) return 'Letters, digits, spaces and - _ . ( ) only.'
  if (n.startsWith('-')) return 'It cannot start with a dash.'
  if (n.endsWith('.')) return 'It cannot end with a dot.'
  if (RESERVED.has(n.split('.')[0].trim().toUpperCase())) return 'Windows keeps that name for a device: choose another.'
  if (taken.some((t) => t.toLowerCase() === n.toLowerCase())) return 'Another project has that name.'
  return ''
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
  if (!r.ok) return ask.info('No project made', r.message ?? 'The engine gave no reason.')
  await st.loadProjects()
  await useApp.getState().selectProject(String(r.data.file))
}

// a project file kept elsewhere (a game's folder, a share): listed, never moved
export async function addProjectFile(): Promise<void> {
  const st = useApp.getState()
  const file = await window.api.pickFile('')
  if (!file) return
  let r = await st.side(['project', 'add', file])
  if (!r.ok && r.code === 'name-taken') {
    const taken = st.projects.map((p) => p.title)
    const name = await ask.prompt(
      'Give it another name',
      `There is a project named ${String(r.data.name)} already. A name for the one in\n${file}\n\nIt is written into that file.`,
      `${String(r.data.name)} 2`,
      'Add it',
      (v) => nameProblem(v, taken)
    )
    if (!name) return
    r = await st.side(['project', 'add', file, `--name=${name}`])
  }
  if (!r.ok) return ask.info('Not added', r.message ?? 'The engine gave no reason.')
  await st.loadProjects()
  await useApp.getState().selectProject(String(r.data.file))
}

// how far an update is: downloading is most of it, installing the rest
function percent(p?: Progress): number {
  if (!p) return 0
  const f = p.total > 0 ? Math.min(1, p.done / p.total) : 1
  if (p.label === 'downloading') return Math.floor(90 * f)
  if (p.label === 'installing') return 90 + Math.floor(10 * f)
  return p.label === 'done' ? 100 : Math.floor(100 * f)
}

function changes(r: UpdateResult, amount: string): string {
  const parts = [r.add ? `${r.add} new` : '', r.replace ? `${r.replace} changed` : '', r.remove ? `${r.remove} removed` : '']
  if (r.download_bytes > 0) parts.push(`${human(r.download_bytes)} ${amount}`)
  return parts.filter(Boolean).join(', ')
}

function StateChip(props: { p: ProjectInfo; r?: ProjectRun }): JSX.Element {
  const { p, r } = props
  if (p.missing) return <Chip kind="bad" icon={CircleAlert}>File not found</Chip>
  if (r?.state === 'waiting') return <Chip kind="muted">Waiting...</Chip>
  if (r?.state === 'checking') return <Chip kind="info">Checking...</Chip>
  if (r?.state === 'updating') return <Chip kind="info">Updating {percent(r.progress)}%</Chip>
  if (r?.state === 'failed') return r.stopped ? <Chip kind="warn">Stopped</Chip> : <Chip kind="bad" icon={CircleAlert}>Failed</Chip>
  if (r?.state === 'done' && r.result) {
    if (r.result.empty || !r.check) return <Chip kind="ok" icon={CircleCheck}>Up to date</Chip>
    return <Chip kind="warn">{changes(r.result, 'to download') || 'An update is waiting'}</Chip>
  }
  if (!p.update_ready) return <Chip kind="muted">{p.publish_src ? 'Releases only' : 'Not set up'}</Chip>
  return <Chip kind="muted">Not checked yet</Chip>
}

function Line(props: { label: string; children: ReactNode }): JSX.Element {
  return (
    <div className="flex gap-3">
      <span className="w-16 shrink-0 pt-0.5 text-[11px] font-semibold uppercase tracking-wider text-ink-400">{props.label}</span>
      <span className="selectable min-w-0 break-all text-ink-300">{props.children}</span>
    </div>
  )
}

function Path(props: { children: ReactNode }): JSX.Element {
  return <span className="font-mono text-[12.5px] text-slate-100">{props.children}</span>
}

function ProjectRow(props: { p: ProjectInfo }): JSX.Element {
  const p = props.p
  const r = useApp((s) => s.runs[p.file])
  const shown = useApp((s) => s.project === p.file)
  const busy = useApp((s) => s.busy)
  const active = running(r)
  // its own check or update, its turn in Update all, or a job on the other pages while it is the one shown
  const held = active || p.missing || (shown && Boolean(busy))

  const update = async (): Promise<void> => {
    if (insecure(p.source_url)) {
      const go = await ask.confirm(
        'Plain http to another machine',
        `${p.source_url} is not https.\n\nEvery file is still checked against the manifest's SHA-256, but the manifest itself could be changed on the way. Continue only on a network you trust, or when the release is signed and you set the publisher key.`,
        'Continue'
      )
      if (!go) return
    }
    useApp.getState().runProject(p.file, false)
  }
  const open = async (): Promise<void> => {
    await useApp.getState().selectProject(p.file)
    useApp.getState().setPage('update')
  }
  const rename = async (): Promise<void> => {
    const st = useApp.getState()
    const taken = st.projects.filter((x) => x.file !== p.file).map((x) => x.title)
    const name = await ask.prompt(
      'Rename the project',
      `Its name in DeltaPatcher's lists. Its settings file stays where it is:\n${p.file}`,
      p.title,
      'Rename',
      (v) => nameProblem(v, taken)
    )
    if (!name || name === p.title) return
    const res = await st.side(['project', 'rename', p.file, name])
    if (!res.ok) return ask.info('Not renamed', res.message ?? 'The engine gave no reason.')
    await st.loadProjects()
  }
  const remove = async (): Promise<void> => {
    const body =
      p.kind === 'folder'
        ? `Its settings file is deleted:\n${p.file}\n\nThe folders it updates and releases stay as they are.`
        : `It leaves the list. Its settings file stays where it is:\n${p.file}`
    if (!(await ask.confirm(`Remove ${p.title}?`, body, 'Remove', true))) return
    const st = useApp.getState()
    const res = await st.side(['project', 'remove', p.file, '--yes'])
    if (!res.ok) return ask.info('Not removed', res.message ?? 'The engine gave no reason.')
    if (st.project === p.file && st.projects[0]) await st.selectProject(st.projects[0].file)
    await useApp.getState().loadProjects()
  }

  const done = r?.state === 'done' && r.result && !r.check && !r.result.empty ? r.result : null
  return (
    <section className={`rounded-xl border bg-ink-850/80 px-5 py-4 shadow-lg shadow-black/20 ${shown ? 'border-delta/40 ring-1 ring-delta/30' : 'border-ink-700'}`}>
      <header className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex min-w-0 items-center gap-2.5 text-sm font-semibold text-slate-100" title={p.file}>
          <FolderKanban size={16} className="shrink-0 text-delta" />
          <span className="truncate">{p.title}</span>
          {shown && <span className="shrink-0 text-[11px] font-normal text-delta-light">selected</span>}
          {p.kind === 'added' && <span className="shrink-0 text-[11px] font-normal text-ink-400">added file</span>}
        </h2>
        <StateChip p={p} r={r} />
      </header>
      <div className="space-y-1.5 text-sm">
        {p.missing ? (
          <Banner kind="bad">Its settings file is gone: {p.file}. Put it back, or remove the project from the list.</Banner>
        ) : (
          <>
            {p.install_dir && (
              <Line label="Update"><Path>{p.install_dir}</Path> <span className="text-ink-400">from</span> <Path>{p.source_url}</Path></Line>
            )}
            {p.publish_src && (
              <Line label="Release">
                <Path>{p.publish_src}</Path> <span className="text-ink-400">to</span>{' '}
                {p.where ? <span className="text-slate-100">{p.where}</span> : <span>where it goes is not set up yet (the Release page)</span>}
              </Line>
            )}
            {!p.install_dir && !p.publish_src && (
              <p className="text-ink-400">Nothing set up yet. Open it, then choose the folder to keep up to date, or the folder to release.</p>
            )}
          </>
        )}
        {r?.state === 'updating' && (
          <div className="h-1.5 overflow-hidden rounded-full bg-ink-700">
            <div className="h-full rounded-full bg-gradient-to-r from-delta to-delta-light transition-all" style={{ width: `${percent(r.progress)}%` }} />
          </div>
        )}
        {r?.state === 'failed' && !r.stopped && (
          <p className="selectable break-all text-xs text-rose-300">{r.message || 'It failed; the console says more.'}</p>
        )}
        {r?.state === 'failed' && r.stopped && <p className="text-xs text-ink-400">Stopped before it was done: the folder is as it was.</p>}
        {done && <p className="text-xs text-ink-400">Updated: {changes(done, 'downloaded')}.</p>}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button small icon={Search} disabled={held || !p.update_ready} onClick={() => useApp.getState().runProject(p.file, true)}>Check</Button>
        <Button small icon={Download} disabled={held || !p.update_ready} onClick={update}>Update</Button>
        <Button small icon={ArrowRight} disabled={p.missing || Boolean(busy)} onClick={open}>Open</Button>
        <div className="ml-auto flex flex-wrap gap-1">
          <Button small variant="ghost" icon={Pencil} disabled={p.missing} onClick={rename}>Rename</Button>
          <Button small variant="ghost" icon={FileSearch} disabled={p.missing} onClick={() => window.api.show(p.file)}>Show file</Button>
          {p.kind !== 'main' && <Button small variant="ghost" icon={Trash2} disabled={active || (shown && Boolean(busy))} onClick={remove}>Remove</Button>}
        </div>
      </div>
    </section>
  )
}

export default function ProjectsPage(): JSX.Element {
  const projects = useApp((s) => s.projects)
  const runs = useApp((s) => s.runs)
  const allJob = useApp((s) => s.allJob)
  const parallel = useApp((s) => s.parallel)
  const ready = projects.filter((p) => p.update_ready)
  const ownJobs = Object.values(runs).some((r) => running(r) && !r.inAll)

  // the list as it is now (another page may have set a folder up), and once a session every project checked
  useEffect(() => {
    useApp.getState().loadProjects().then(() => {
      const st = useApp.getState()
      if (st.autoChecked) return
      useApp.setState({ autoChecked: true })
      if (!st.allJob && st.projects.some((p) => p.update_ready)) st.runAll(true)
    })
  }, [])

  const updateAll = async (): Promise<void> => {
    const plain = ready.filter((p) => insecure(p.source_url))
    if (plain.length) {
      const go = await ask.confirm(
        'Plain http to another machine',
        `${plain.map((p) => `${p.title}: ${p.source_url}`).join('\n')}\n\nEvery file is still checked against the manifest's SHA-256, but the manifest itself could be changed on the way. Continue only on a network you trust, or when the release is signed and the publisher key is set.`,
        'Continue'
      )
      if (!go) return
    }
    useApp.getState().runAll(false)
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          {allJob ? (
            <Button variant="primary" icon={Square} onClick={() => useApp.getState().stopAll()}>Stop</Button>
          ) : (
            <Button variant="primary" icon={Download} disabled={!ready.length || ownJobs} onClick={updateAll}>Update all</Button>
          )}
          <Button icon={Search} disabled={Boolean(allJob) || !ready.length || ownJobs} onClick={() => useApp.getState().runAll(true)}>Check all</Button>
          {allJob && allJob.total > 0 && (
            <Chip kind="info" icon={Boxes}>{allJob.check ? 'Checked' : 'Updated'} {allJob.done} of {allJob.total}</Chip>
          )}
          <div className="ml-auto flex flex-wrap gap-2">
            <Button variant="ghost" icon={Plus} onClick={newProject}>New project</Button>
            <Button variant="ghost" icon={FilePlus} onClick={addProjectFile}>Add a project file</Button>
          </div>
        </div>
        <p className="hint">
          Update all brings every project with a folder and a source up to date, {parallel === 1 ? 'one at a time' : `${parallel} at the same time`}
          {' '}(parallel_updates, on the Settings page). Each project keeps its own folders and settings.
        </p>
      </Card>
      {projects.map((p) => (
        <ProjectRow key={p.file} p={p} />
      ))}
    </div>
  )
}
