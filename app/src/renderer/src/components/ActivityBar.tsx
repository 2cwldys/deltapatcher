// The bottom of the window: what is running, its progress, Stop, and the log console. Update all (and
// Check all) runs beside the other pages' jobs: it shows here while none of those does.
import { useEffect, useRef } from 'react'
import { ChevronDown, ChevronUp, FolderOpen, Square, Terminal } from 'lucide-react'
import { useApp } from '../store/app'
import { Button, human } from './ui'

const STYLE: Record<string, string> = {
  ok: 'text-slate-100',
  warn: 'text-blob',
  err: 'text-rose-300',
  code: 'text-violet-300',
  head: 'text-delta-light font-semibold'
}

export default function ActivityBar(): JSX.Element {
  const { busy, progress, status, statusKind, cancellable, consoleOpen, logs, logDir, allJob } = useApp()
  const cancel = useApp((s) => s.cancel)
  const stopAll = useApp((s) => s.stopAll)
  const toggle = useApp((s) => s.toggleConsole)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (consoleOpen) end.current?.scrollIntoView({ block: 'end' })
  }, [logs.length, consoleOpen])

  const all = !busy && allJob ? allJob : null
  const pct = all
    ? all.total > 0 ? Math.min(100, (100 * all.done) / all.total) : null
    : progress && progress.total > 0 ? Math.min(100, (100 * progress.done) / progress.total) : busy ? null : 0
  const amount = all
    ? all.total > 0 ? `${all.done} of ${all.total} projects` : ''
    : progress && (progress.label === 'downloading' || progress.label === 'uploading')
      ? `${human(progress.done)} of ${human(progress.total)}`
      : progress
        ? `${progress.done} of ${progress.total} ${progress.label === 'projects' ? 'projects' : 'files'}`
        : ''
  const dot =
    statusKind === 'busy' ? 'bg-blob animate-pulse' : statusKind === 'ok' ? 'bg-delta' : statusKind === 'err' ? 'bg-rose-400' : 'bg-ink-500'

  return (
    <div className="border-t border-ink-700/80 bg-ink-900/95">
      {consoleOpen && (
        <div className="selectable h-56 overflow-y-auto border-b border-ink-700/80 bg-ink-950 px-4 py-3 font-mono text-[12px] leading-relaxed">
          {logs.length === 0 && <div className="text-ink-400">Nothing yet. What DeltaPatcher does shows here, and in its log file.</div>}
          {logs.map((l) => (
            <div key={l.id} className="flex gap-3">
              <span className="shrink-0 text-ink-500">{l.time}</span>
              <span className={`whitespace-pre-wrap break-all ${l.style ? STYLE[l.style] ?? 'text-ink-300' : 'text-ink-300'}`}>{l.text}</span>
            </div>
          ))}
          <div ref={end} />
        </div>
      )}
      <div className="flex items-center gap-4 px-5 py-3">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4 text-xs">
            <span className="truncate text-slate-200">{status}</span>
            <span className="shrink-0 tabular-nums text-ink-400">{amount}</span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-700">
            {pct === null ? (
              <div className="h-full w-1/3 animate-pulse rounded-full bg-gradient-to-r from-delta/40 via-delta to-delta/40" />
            ) : (
              <div className="h-full rounded-full bg-gradient-to-r from-delta to-delta-light transition-all" style={{ width: `${pct}%` }} />
            )}
          </div>
        </div>
        {busy && cancellable && (
          <Button small variant="secondary" icon={Square} onClick={cancel}>
            Stop
          </Button>
        )}
        {all && (
          <Button small variant="secondary" icon={Square} onClick={stopAll}>
            Stop
          </Button>
        )}
        {logDir && <Button small variant="ghost" icon={FolderOpen} onClick={() => window.api.open(logDir)} title="Open the log folder">Logs</Button>}
        <Button small variant="ghost" icon={Terminal} onClick={toggle}>
          Console {consoleOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </Button>
      </div>
    </div>
  )
}
