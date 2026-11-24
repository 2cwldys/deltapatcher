// The Projects page: every project at a glance with its state. Check or update one, or all of them at once
// (several at the same time, each with its own progress); open one on the other pages; make, add, import,
// export, rename and remove projects. A long list gets compact rows; the filter finds one.
import { ReactNode, useEffect, useState } from 'react'
import {
  ArrowRight, Boxes, CircleAlert, CircleCheck, Download, FilePlus, FileSearch, FolderKanban, Pencil, Plus, Search,
  Share2, Square, Trash2
} from 'lucide-react'
import { ProjectInfo, ProjectRun, Progress, UpdateResult, running, useApp } from '../store/app'
import { ask } from '../store/modal'
import { newProject, openProjectFile, removeAll, removeProject, renameProject, startExport } from '../store/projectActions'
import { Banner, Button, Card, Chip, human } from '../components/ui'
import { insecure } from './UpdatePage'
import { anotherRun } from '../store/schedule'

// more projects than this get compact rows
const COMPACT = 6

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

function ProjectRow(props: { p: ProjectInfo; compact: boolean }): JSX.Element {
  const p = props.p
  const r = useApp((s) => s.runs[p.file])
  const shown = useApp((s) => s.project === p.file)
  const busy = useApp((s) => s.busy)
  const active = running(r)
  // its own check or update, its turn in Update all, or a job on the other pages while it is the one shown
  const held = active || p.missing || (shown && Boolean(busy))
  const label = (text: string): string | undefined => (props.compact ? undefined : text)

  const update = async (): Promise<void> => {
    if (insecure(p.source_url)) {
      const go = await ask.confirm(
        'Plain http to another machine',
        `${p.source_url} is not https.\n\nEvery file is still checked against the manifest's SHA-256.\nThe manifest itself could be changed on the way.\nContinue only on a network you trust, or when the release is signed and you set the publisher key.`,
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
  const done = r?.state === 'done' && r.result && !r.check && !r.result.empty ? r.result : null
  const buttons = (
    <>
      <Button small icon={Search} title="Check" disabled={held || !p.update_ready} onClick={() => useApp.getState().runProject(p.file, true)}>
        {label('Check')}
      </Button>
      <Button small icon={Download} title="Update" disabled={held || !p.update_ready} onClick={update}>{label('Update')}</Button>
      <Button small icon={ArrowRight} title="Open it on the Update page" disabled={p.missing || Boolean(busy)} onClick={open}>
        {label('Open')}
      </Button>
      <div className={props.compact ? 'ml-2 flex gap-1' : 'ml-auto flex flex-wrap gap-1'}>
        <Button small variant="ghost" icon={Share2} title="Export it to a project file" disabled={p.missing || !p.address}
          onClick={() => startExport(p.file)}>{label('Export...')}</Button>
        <Button small variant="ghost" icon={Pencil} title="Rename" disabled={p.missing} onClick={() => renameProject(p)}>
          {label('Rename')}
        </Button>
        <Button small variant="ghost" icon={FileSearch} title="Show its settings file" disabled={p.missing}
          onClick={() => window.api.show(p.file)}>{label('Show file')}</Button>
        {p.kind !== 'main' && (
          <Button small variant="remove" icon={Trash2} title="Remove this project" disabled={active || (shown && Boolean(busy))}
            onClick={() => removeProject(p)}>{label('Remove')}</Button>
        )}
      </div>
    </>
  )

  if (props.compact) {
    return (
      <section className={`flex items-center gap-3 rounded-lg border bg-ink-850/80 px-4 py-2 ${shown ? 'border-delta/40 ring-1 ring-delta/30' : 'border-ink-700'}`}>
        <FolderKanban size={15} className="shrink-0 text-delta" />
        <span className="min-w-0 max-w-[13rem] shrink-0 truncate text-sm font-semibold text-slate-100" title={p.file}>{p.title}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-400" title={p.install_dir || p.publish_src}>
          {p.install_dir || p.publish_src || 'not set up yet'}
        </span>
        <span className="shrink-0"><StateChip p={p} r={r} /></span>
        <div className="flex shrink-0 items-center gap-1">{buttons}</div>
      </section>
    )
  }

  return (
    <section className={`rounded-xl border bg-ink-850/80 px-5 py-4 shadow-lg shadow-black/20 ${shown ? 'border-delta/40 ring-1 ring-delta/30' : 'border-ink-700'}`}>
      <header className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex min-w-0 items-center gap-2.5 text-sm font-semibold text-slate-100" title={p.file}>
          <FolderKanban size={16} className="shrink-0 text-delta" />
          <span className="truncate">{p.title}</span>
          {shown && <span className="shrink-0 text-[11px] font-normal text-delta-light">selected</span>}
          {p.kind === 'added' && <span className="shrink-0 text-[11px] font-normal text-ink-400">added settings file</span>}
        </h2>
        <StateChip p={p} r={r} />
      </header>
      <div className="space-y-1.5 text-sm">
        {p.missing ? (
          <Banner kind="bad">
            Its settings file is gone: {p.file}.
            <br />
            Put it back, or remove the project from the list.
          </Banner>
        ) : (
          <>
            {p.description && <p className="text-ink-300">{p.description}</p>}
            {p.install_hint && <p className="text-ink-400">{p.install_hint}</p>}
            {p.install_folder && (
              <Line label="Folder"><Path>{p.install_folder}</Path> <span className="text-ink-400">inside the folder a player picks</span></Line>
            )}
            {p.install_dir && (
              <Line label="Update"><Path>{p.install_dir}</Path> <span className="text-ink-400">from</span> <Path>{p.source_url}</Path></Line>
            )}
            {p.publish_src && (
              <Line label="Release">
                <Path>{p.publish_src}</Path> <span className="text-ink-400">to</span>{' '}
                {p.where ? <span className="text-slate-100">{p.where}</span> : <span>where it goes is not set up yet (the Release page)</span>}
              </Line>
            )}
            {p.website && (
              <Line label="Website">
                <a href={p.website} target="_blank" rel="noreferrer" className="text-delta-light hover:underline">{p.website}</a>
              </Line>
            )}
            {!p.install_dir && !p.publish_src && (
              <p className="whitespace-pre-line text-ink-400">
                {'Nothing set up yet.\nOpen it, then choose the folder to keep up to date, or the folder to release.'}
              </p>
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
      <div className="mt-3 flex flex-wrap items-center gap-2">{buttons}</div>
    </section>
  )
}

export default function ProjectsPage(): JSX.Element {
  const projects = useApp((s) => s.projects)
  const runs = useApp((s) => s.runs)
  const allJob = useApp((s) => s.allJob)
  const parallel = useApp((s) => s.parallel)
  const busy = useApp((s) => s.busy)
  const [filter, setFilter] = useState('')
  const ready = projects.filter((p) => p.update_ready)
  const ownJobs = Object.values(runs).some((r) => running(r) && !r.inAll)
  const words = filter.trim().toLowerCase()
  const listed = words
    ? projects.filter((p) => [p.title, p.install_dir, p.publish_src, p.source_url, p.description].some((t) => (t ?? '').toLowerCase().includes(words)))
    : projects
  const compact = listed.length > COMPACT

  // the list as it is now (another page may have set a folder up), and every project checked once a session
  useEffect(() => {
    useApp.getState().loadProjects().then(() => {
      const st = useApp.getState()
      if (st.autoChecked) return
      useApp.setState({ autoChecked: true })
      if (!anotherRun(st) && st.projects.some((p) => p.update_ready)) st.runAll(true)
    })
  }, [])

  const updateAll = async (): Promise<void> => {
    const plain = ready.filter((p) => insecure(p.source_url))
    if (plain.length) {
      const go = await ask.confirm(
        'Plain http to another machine',
        `${plain.map((p) => `${p.title}: ${p.source_url}`).join('\n')}\n\nEvery file is still checked against the manifest's SHA-256.\nThe manifest itself could be changed on the way.\nContinue only on a network you trust, or when the release is signed and the publisher key is set.`,
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
            <Button variant="ghost" icon={FilePlus} onClick={openProjectFile}>Add a project file...</Button>
            <Button variant="ghost" icon={Share2} disabled={!projects.some((p) => p.address)} onClick={() => startExport()}>Export...</Button>
            <Button variant="remove" icon={Trash2} disabled={projects.length < 2 || Boolean(allJob) || ownJobs || Boolean(busy)}
              onClick={removeAll}>Remove all</Button>
          </div>
        </div>
        <div className="relative mt-4">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
          <input className="input pl-9" placeholder="Filter projects by name, folder or address" value={filter} spellCheck={false}
            onChange={(e) => setFilter(e.target.value)} />
        </div>
        <p className="hint">
          {`Update all brings every project with a folder and a source up to date, ${parallel === 1 ? 'one at a time' : `${parallel} at the same time`} (parallel_updates, on the Settings page).\n` +
            "Add a project file takes a .deltapatch or .html file from a publisher, or (advanced) a project's .ini settings file.\n" +
            'Export... writes a project file to give to players.'}
        </p>
      </Card>
      {listed.map((p) => (
        <ProjectRow key={p.file} p={p} compact={compact} />
      ))}
      {words && !listed.length && <p className="text-sm text-ink-400">No project matches {filter.trim()}.</p>}
    </div>
  )
}
