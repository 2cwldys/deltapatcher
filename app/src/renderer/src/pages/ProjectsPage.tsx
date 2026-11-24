// The Projects page: every project at a glance with its state. Check or update one, or all of them at once
// (several at the same time, each with its own progress); open one on the other pages; make, add, import,
// export, rename and remove projects. Each project shows its main action (Update, or Open while it has no
// folder to update) and a "..." menu with every other one, What changed among them. A project its publisher
// changed says so, with Apply and Not now. A long list gets compact rows; the filter finds one.
import { ReactNode, useEffect, useState } from 'react'
import {
  ArrowRight, BellRing, Boxes, Check, CircleAlert, CircleCheck, Clock, Download, FileDiff, FilePlus, FileSearch,
  FolderKanban, Pencil, Plus, Search, Share2, Square, Trash2
} from 'lucide-react'
import { ProjectInfo, ProjectRun, Progress, UpdateResult, running, useApp } from '../store/app'
import { ask } from '../store/modal'
import {
  applyPublisher, newProject, notNowPublisher, openProjectFile, removeAll, removeProject, renameProject, showChanges,
  startExport
} from '../store/projectActions'
import { hasWhatChanged, promptLine, promptOf } from '../store/publisher'
import { Banner, Button, Card, Chip, InfoTip, Menu, MenuItem, human } from '../components/ui'
import PublisherPrompt from '../components/PublisherPrompt'
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
  const noFolder = 'It needs a folder to keep up to date and a source: open it to set them'
  // what its publisher changed in it, found by its last check or update, until Apply or Not now answers it
  const change = promptOf(r)
  const result = r?.state === 'done' ? r.result : undefined

  // the main action: Update when it can update, else Open (where its folders are set up); the rest in the menu
  const items: MenuItem[] = [
    { label: 'Check', icon: Search, disabled: held || !p.update_ready, title: p.update_ready ? 'Check' : noFolder,
      onClick: () => useApp.getState().runProject(p.file, true) },
    p.update_ready
      ? { label: 'Open', icon: ArrowRight, title: 'Open it on the Update page', disabled: p.missing || Boolean(busy), onClick: () => void open() }
      : { label: 'Update', icon: Download, title: noFolder, disabled: true, onClick: () => void update() },
    { label: 'What changed', icon: FileDiff, disabled: !hasWhatChanged(result),
      title: hasWhatChanged(result) ? 'The files its release changes, and the notes from its publisher' : 'Check it first: then what its release changes shows here',
      onClick: () => result && showChanges(p.title, result) },
    { label: 'Export...', icon: Share2, title: 'Export it to a project file', disabled: p.missing || !p.address, onClick: () => void startExport(p.file) },
    { label: 'Rename', icon: Pencil, title: 'Rename', disabled: p.missing, onClick: () => void renameProject(p) },
    { label: 'Show file', icon: FileSearch, title: 'Show its settings file', disabled: p.missing, onClick: () => void window.api.show(p.file) }
  ]
  if (change && props.compact) {
    items.splice(2, 0,
      { label: "Apply the publisher's change...", icon: Check, title: promptLine(change), disabled: Boolean(busy),
        onClick: () => void applyPublisher(p.file, change) },
      { label: 'Not now', icon: Clock, title: 'Asked again once the publisher changes it again', disabled: Boolean(busy),
        onClick: () => void notNowPublisher(p.file, change) })
  }
  if (p.kind !== 'main') {
    items.push({ label: 'Remove', icon: Trash2, title: 'Remove this project', danger: true, disabled: active || (shown && Boolean(busy)),
      onClick: () => void removeProject(p) })
  }
  const label = (text: string): string | undefined => (props.compact ? undefined : text)
  const main = p.update_ready ? (
    <Button small icon={Download} title="Update" disabled={held} onClick={update}>{label('Update')}</Button>
  ) : (
    <Button small icon={ArrowRight} title="Open it on the Update page" disabled={p.missing || Boolean(busy)} onClick={open}>{label('Open')}</Button>
  )
  const actions = (
    <>
      {main}
      <Menu small title={`More for ${p.title}`} items={items} />
    </>
  )

  if (props.compact) {
    return (
      <section className={`flex items-center gap-3 rounded-lg border bg-ink-850/80 px-4 py-2 ${shown ? 'border-delta/40 ring-1 ring-delta/30' : 'border-ink-700'}`}>
        <FolderKanban size={15} className="shrink-0 text-delta" />
        <span className="min-w-0 max-w-[11rem] shrink-0 truncate text-sm font-semibold text-slate-100" title={p.file}>{p.title}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-400" title={p.install_dir || p.publish_src}>
          {p.install_dir || p.publish_src || 'not set up yet'}
        </span>
        {change && <span className="flex min-w-0 shrink-0" title={promptLine(change)}><Chip kind="info" icon={BellRing}>Changed by its publisher</Chip></span>}
        <span className="flex min-w-0 shrink justify-end"><StateChip p={p} r={r} /></span>
        <div className="flex shrink-0 items-center gap-1">{actions}</div>
      </section>
    )
  }

  return (
    <Card fold={`projects/${p.file}`} title={p.title} titleHint={p.file} icon={FolderKanban} ring={shown}
      marks={
        <>
          {shown && <span className="shrink-0 text-[11px] font-normal text-delta-light">selected</span>}
          {p.kind === 'added' && <span className="shrink-0 text-[11px] font-normal text-ink-400">added settings file</span>}
        </>
      }
      actions={<><span className="flex min-w-0 shrink"><StateChip p={p} r={r} /></span>{actions}</>}>
      <div className="space-y-1.5 text-sm">
        {change && <div className="pb-1.5"><PublisherPrompt file={p.file} change={change} /></div>}
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
                <Path>{p.publish_src}</Path>{' '}
                {p.where ? <><span className="text-ink-400">to</span> <span className="text-slate-100">{p.where}</span></>
                  : <span>{'(where it goes is not set up yet: the Release page)'}</span>}
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
    </Card>
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
      <div className="space-y-3">
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
          <InfoTip text={`Update all brings every project with a folder and a source up to date, ${parallel === 1 ? 'one at a time' : `${parallel} at the same time`} (parallel_updates, on the Settings page).\n` +
            "Add a project file takes a .deltapatch or .html file from a publisher, or (advanced) a project's .ini settings file.\n" +
            'Export... writes a project file to give to players.'} />
          <div className="ml-auto flex items-center gap-2">
            <Menu label="Add..." icon={Plus} title="Add a project" items={[
              { label: 'Add a project file...', icon: FilePlus, title: 'A .deltapatch or .html file from a publisher, or a .ini settings file',
                onClick: () => void openProjectFile() },
              { label: 'New project...', icon: Plus, title: 'A new project of your own', onClick: () => void newProject() }
            ]} />
            <Menu title="More: export, remove all" items={[
              { label: 'Export...', icon: Share2, title: 'Export projects to a project file', disabled: !projects.some((p) => p.address),
                onClick: () => void startExport() },
              { label: 'Remove all', icon: Trash2, title: 'Remove every project but Default', danger: true,
                disabled: projects.length < 2 || Boolean(allJob) || ownJobs || Boolean(busy), onClick: () => void removeAll() }
            ]} />
          </div>
        </div>
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
          <input className="input pl-9" placeholder="Filter projects by name, folder or address" value={filter} spellCheck={false}
            onChange={(e) => setFilter(e.target.value)} />
        </div>
      </div>
      {listed.map((p) => (
        <ProjectRow key={p.file} p={p} compact={compact} />
      ))}
      {words && !listed.length && <p className="text-sm text-ink-400">No project matches {filter.trim()}.</p>}
    </div>
  )
}
