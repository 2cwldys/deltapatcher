// The Settings page's Backups section: copies of the live release, read from where players download it and
// kept on this computer. Back up now; a line that sums the backups up, opening on a click to the list (each
// one's day in UTC, release, files, size, and Restore... after a question that says what it replaces there);
// the system scheduler's last run, a backup that cannot be read and the last failure, a short line each; then
// the section's settings (the children).
import { ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { Archive, ArchiveRestore, ChevronDown, ChevronRight } from 'lucide-react'
import { FORCE_STOPPED } from '@shared/forceStop'
import { noteBackup, useApp } from '../store/app'
import { ask, HostKey } from '../store/modal'
import { sentences } from '../store/projectActions'
import { BackupInfo, BackupList, backupRows, backupSummary, ReleaseShort, restoreQuestion, scheduledLine } from '../store/backups'
import { confirmHost, modeOf, withHostTrust } from '../pages/ReleasePage'
import { Button, Card, human } from './ui'

// the columns of the list: day, release, files, size, Restore...
const ROW = 'grid grid-cols-[6.5rem_minmax(0,1fr)_3.5rem_5rem_6.5rem] items-center gap-x-3 px-3'

export default function BackupsSection(props: { children: ReactNode }): JSX.Element {
  const project = useApp((s) => s.project)
  const busy = useApp((s) => s.busy)
  const backupJob = useApp((s) => s.backupJob)
  const failed = useApp((s) => s.backupProblems[s.project] ?? '')
  const mode = useApp((s) => modeOf(s.settings.deploy_mode))
  const backupDir = useApp((s) => String(s.settings.backup_dir ?? ''))
  const every = useApp((s) => s.settings.backup_every_hours)
  const [list, setList] = useState<BackupList | null>(null)
  const [problem, setProblem] = useState('')
  const [open, setOpen] = useState(false)
  const asked = useRef(0)

  const load = useCallback(async (): Promise<void> => {
    const n = ++asked.current
    const r = await useApp.getState().run('', ['backup', 'list'], { quiet: true })
    if (n !== asked.current) return
    if (r.ok) {
      setList(r.data as unknown as BackupList)
      setProblem('')
    } else {
      setList(null)
      setProblem(String(r.message ?? ''))
    }
  }, [])
  useEffect(() => {
    void load()
  }, [project, backupJob, load])
  // another backup folder typed: listed again once it is saved
  const dirSeen = useRef({ project, backupDir })
  useEffect(() => {
    const before = dirSeen.current
    dirSeen.current = { project, backupDir }
    if (before.project !== project || before.backupDir === backupDir) return
    const t = setTimeout(() => void load(), 1500)
    return () => clearTimeout(t)
  }, [project, backupDir, load])

  // a backup now: what it did shows here, and the projects are read again (when each was backed up)
  const now = async (): Promise<void> => {
    const file = useApp.getState().project
    const d = await withHostTrust('Back up', ['backup', 'now'], undefined, (r) => {
      if (r.code !== FORCE_STOPPED) noteBackup(file, r.message ?? 'failed')
    })
    if (d) noteBackup(file, null)
    await Promise.all([load(), useApp.getState().loadProjects()])
    if (d && !d.made) await ask.info('No new backup', 'The live release has not changed since its last backup.')
  }
  const restore = async (name: string): Promise<void> => {
    const st = useApp.getState()
    for (let attempt = 0; attempt < 3; attempt++) {
      const r = await st.run('', ['backup', 'restore', name], { quiet: true })
      if (r.code === 'host-unknown') {
        if (!(await confirmHost(String(r.data.host ?? ''), (r.data.keys ?? []) as HostKey[], false))) return
        continue
      }
      if (r.code !== 'confirm-needed') {
        await ask.info('The backup cannot be restored', sentences(String(r.message ?? 'The engine gave no reason.')))
        return
      }
      const q = restoreQuestion(r.data as { backup?: BackupInfo; live?: ReleaseShort | null; live_problem?: string; destination?: string })
      if (!(await ask.confirm(q.title, q.body, 'Restore it', true))) return
      await withHostTrust('Restore a backup', ['backup', 'restore', name, '--yes'])
      return
    }
  }

  const rows = backupRows(list)
  const working = Boolean(busy) || Boolean(backupJob)
  const today = new Date().toISOString().slice(0, 10)
  return (
    <Card title="Backups" icon={Archive}
      actions={<Button small icon={Archive} disabled={working || mode === 'local'} onClick={now}>
        {backupJob === project ? 'Backing up...' : 'Back up now'}
      </Button>}>
      {mode === 'local' ? (
        <p className="text-sm text-ink-300">A release served from this computer has nothing to back up: its store is here already.</p>
      ) : (
        <button type="button" aria-expanded={open} disabled={!rows.length} onClick={() => setOpen(!open)}
          className="flex w-full items-center gap-2 rounded-lg text-left text-sm text-slate-100 disabled:cursor-default">
          {rows.length > 0 && (open ? <ChevronDown size={15} className="shrink-0 text-delta" /> : <ChevronRight size={15} className="shrink-0 text-delta" />)}
          <span>{list ? backupSummary(list, every, today, human(list.bytes)) : problem ? 'The backups could not be listed.' : 'Reading the backups...'}</span>
          {rows.length > 0 && <span className="text-xs text-ink-400">{open ? 'hide' : 'show'}</span>}
        </button>
      )}
      {problem && <p className="mt-2 text-xs text-blob-light">{problem}</p>}
      {failed && <p className="mt-2 text-xs text-rose-300">{`The last backup failed: ${failed}`}</p>}
      {(list?.unreadable ?? []).map((u) => (
        <p key={u.name} className="mt-2 text-xs text-blob-light">{`${u.name} cannot be read (${u.why}): nothing is removed until it can be.`}</p>
      ))}
      {list?.scheduled && <p className={`mt-2 text-xs ${list.scheduled.ok ? 'text-ink-400' : 'text-blob-light'}`}>{scheduledLine(list.scheduled)}</p>}
      {open && list && rows.length > 0 && (
        <div className="mt-3 overflow-hidden rounded-lg border border-ink-700">
          <div className={`${ROW} bg-ink-900/60 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-400`}>
            <span>Day (UTC)</span><span>Release</span><span className="text-right">Files</span><span className="text-right">Size</span><span />
          </div>
          {rows.map((b) => (
            <div key={b.name} className={`${ROW} border-t border-ink-700/70 py-1 text-sm`}>
              <span className="font-mono text-slate-100" title={`Made ${b.made}`}>{b.name}</span>
              <span className="truncate text-ink-300" title={b.release}>{b.release}</span>
              <span className="text-right tabular-nums text-ink-400">{b.files}</span>
              <span className="text-right tabular-nums text-ink-400">{human(b.bytes)}</span>
              <Button small variant="ghost" icon={ArchiveRestore} disabled={working} onClick={() => restore(b.name)}>Restore...</Button>
            </div>
          ))}
          <p className="selectable truncate border-t border-ink-700/70 px-3 py-1.5 text-xs text-ink-400" title={list.folder}>{`In ${list.folder}`}</p>
        </div>
      )}
      <div className="mt-5 grid gap-5 border-t border-ink-700/70 pt-5 md:grid-cols-2">{props.children}</div>
    </Card>
  )
}
