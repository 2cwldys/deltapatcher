import { useEffect, useState } from 'react'
import { ArrowUpCircle, BookOpen, Boxes, Bug, CircleHelp, RefreshCw, Rocket, SlidersHorizontal, LucideIcon } from 'lucide-react'
import { Page, useApp, waitingCount } from '../store/app'
import { REPO_URL } from '@shared/project'
import ProjectPicker from './ProjectPicker'

const LINKS: { page: Page; label: string; hint: string; Icon: LucideIcon }[] = [
  { page: 'projects', label: 'Projects', hint: 'Every game, mod or server', Icon: Boxes },
  { page: 'update', label: 'Update', hint: "The picked project's folder", Icon: RefreshCw },
  { page: 'release', label: 'Release', hint: 'Send players a new version', Icon: Rocket },
  { page: 'settings', label: 'Settings', hint: 'deltapatcher.ini', Icon: SlidersHorizontal }
]

// Help, at the bottom: the wiki, reporting an issue, and the version, folded into one menu
function HelpMenu(): JSX.Element {
  const [open, setOpen] = useState(false)
  const [version, setVersion] = useState('')
  useEffect(() => {
    window.api.info().then((i) => setVersion(i.version))
  }, [])
  const item = 'flex items-center gap-2 px-3 py-2 text-xs text-ink-300 transition-colors hover:bg-ink-700/60 hover:text-slate-100'
  return (
    <div className="relative mt-3">
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full left-0 right-0 z-40 mb-1 overflow-hidden rounded-lg border border-ink-600 bg-ink-850 py-1 shadow-xl shadow-black/50">
            <a href={`${REPO_URL}/wiki`} target="_blank" rel="noreferrer" className={item} onClick={() => setOpen(false)}>
              <BookOpen size={14} className="text-delta-light" />
              Read the wiki
            </a>
            <a href={`${REPO_URL}/issues/new`} target="_blank" rel="noreferrer" className={item} onClick={() => setOpen(false)}>
              <Bug size={14} className="text-blob-light" />
              Report an issue
            </a>
            <div className="mt-1 border-t border-ink-700 px-3 pb-1 pt-2 font-mono text-[11px] tracking-wider text-ink-400">
              DeltaPatcher {version ? `v${version}` : ''}
            </div>
          </div>
        </>
      )}
      <button type="button" onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 rounded-lg px-1 py-1.5 text-xs text-ink-300 hover:text-slate-100">
        <CircleHelp size={14} className="text-delta-light" />
        Help
      </button>
    </div>
  )
}

export default function Sidebar(): JSX.Element {
  const page = useApp((s) => s.page)
  const setPage = useApp((s) => s.setPage)
  const appUpdate = useApp((s) => s.appUpdate)
  const installAppUpdate = useApp((s) => s.installAppUpdate)
  const busy = useApp((s) => s.busy)
  // the projects the last check found an update for
  const waiting = useApp(waitingCount)

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-ink-700/80 bg-ink-900/95">
      <div className="flex items-center gap-3 px-5 pb-6 pt-6">
        <img src="./logo.png" alt="" className="h-10 w-10 drop-shadow-lg" draggable={false} />
        <div>
          <div className="text-[15px] font-semibold leading-tight tracking-tight">Delta Patcher</div>
          <div className="text-[11px] text-ink-400">SHA-256 delta patching</div>
        </div>
      </div>
      <ProjectPicker />
      <nav className="flex flex-col gap-1 px-3">
        {LINKS.map(({ page: p, label, hint, Icon }) => {
          const on = p === page
          return (
            <button
              key={p}
              type="button"
              onClick={() => setPage(p)}
              className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${
                on ? 'bg-delta/15 text-slate-100 ring-1 ring-delta/30' : 'text-ink-300 hover:bg-ink-800 hover:text-slate-100'
              }`}
            >
              <Icon size={17} className={on ? 'text-delta' : 'text-ink-400 group-hover:text-ink-300'} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{label}</span>
                <span className="block text-[11px] text-ink-400">{hint}</span>
              </span>
              {p === 'projects' && waiting > 0 && (
                <span title={`${waiting} ${waiting === 1 ? 'project has' : 'projects have'} an update`}
                  className="shrink-0 rounded-full bg-blob px-1.5 text-[11px] font-semibold leading-5 tabular-nums text-ink-950">
                  {waiting}
                </span>
              )}
            </button>
          )
        })}
      </nav>
      {appUpdate && (
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={installAppUpdate}
          title={`DeltaPatcher ${appUpdate.latest} is out`}
          className="mx-3 mt-4 flex items-center gap-3 rounded-lg border border-blob/40 bg-blob/10 px-3 py-2.5 text-left text-blob-light transition-colors hover:bg-blob/20 disabled:opacity-50"
        >
          <ArrowUpCircle size={17} className="shrink-0" />
          <span>
            <span className="block text-sm font-medium">Update DeltaPatcher</span>
            <span className="block font-mono text-[11px] text-ink-300">
              {appUpdate.current} {'\u2192'} {appUpdate.latest}
            </span>
          </span>
        </button>
      )}
      <div className="mt-auto px-5 pb-5">
        <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-[11px] leading-relaxed text-ink-400">
          Every file is checked against its <span className="text-blob">SHA-256</span> before it is used.
        </div>
        <HelpMenu />
      </div>
    </aside>
  )
}
