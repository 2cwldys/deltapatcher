// Force stop, the panic button: the red box at the bottom of the sidebar ends everything DeltaPatcher runs, and
// the pages stay greyed out under a notice until the stop is lifted. The box stays clickable over a dialog.
import { OctagonX } from 'lucide-react'
import { useApp } from '../store/app'
import { Button } from './ui'

// when Force stop was turned on, for people ('' when that is not known)
function sinceText(since: string): string {
  const d = new Date(since)
  if (!since || Number.isNaN(d.getTime())) return ''
  return d.toLocaleString([], { hour12: false, year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function ForceStopBox(): JSX.Element {
  const stopped = useApp((s) => s.stopped)
  const forceStop = useApp((s) => s.forceStop)
  const liftStop = useApp((s) => s.liftStop)

  if (stopped) {
    return (
      <div className="relative z-[60] mb-3 rounded-lg border border-rose-500/70 bg-rose-500/15 px-3 py-2.5">
        <div className="flex items-center gap-2.5 text-sm font-semibold text-rose-100">
          <OctagonX size={18} className="shrink-0 text-rose-400" />
          Everything is stopped
        </div>
        <button
          type="button"
          onClick={() => void liftStop()}
          className="mt-2 w-full rounded-md border border-rose-400/50 bg-ink-900/60 px-2 py-1.5 text-xs font-medium text-rose-100 transition-colors hover:bg-rose-500/25"
        >
          Lift the stop
        </button>
      </div>
    )
  }
  return (
    <button
      type="button"
      onClick={() => void forceStop()}
      title="Stops every update, check, release and download at once, until you lift the stop"
      className="relative z-[60] mb-3 flex w-full items-center gap-3 rounded-lg border border-rose-500/60 bg-rose-500/10 px-3 py-2.5 text-left text-rose-100 transition-colors hover:bg-rose-500/25"
    >
      <OctagonX size={18} className="shrink-0 text-rose-400" />
      <span>
        <span className="block text-sm font-semibold">Force stop</span>
        <span className="block text-[11px] text-rose-200/70">Stops everything at once</span>
      </span>
    </button>
  )
}

// over the greyed-out pages while Force stop is on
export function StoppedNotice(): JSX.Element {
  const since = useApp((s) => s.stoppedSince)
  const liftStop = useApp((s) => s.liftStop)
  const at = sinceText(since)
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center p-8">
      <div className="max-w-md rounded-xl border border-rose-500/50 bg-ink-900/95 p-6 text-center shadow-xl shadow-black/40">
        <OctagonX size={40} className="mx-auto text-rose-400" />
        <h2 className="mt-3 text-lg font-semibold text-slate-100">Force stop is on{at ? ` since ${at}` : ''}</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-300">
          Every update, check, release and download was stopped, and nothing starts until you lift the stop.
        </p>
        <p className="mt-3 text-sm leading-relaxed text-ink-300">
          Lifting it starts nothing that was stopped; the automatic checks come back on their usual schedule.
        </p>
        <div className="mt-4">
          <Button variant="danger" onClick={() => void liftStop()}>
            Lift the stop
          </Button>
        </div>
      </div>
    </div>
  )
}
