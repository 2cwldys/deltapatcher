// The project picker at the top of the sidebar: the project Update, Release and Settings show, and a list
// to pick another, make a new one, or add a project file kept elsewhere.
import { useEffect, useState } from 'react'
import { Check, ChevronsUpDown, FilePlus, FolderKanban, Plus } from 'lucide-react'
import { useApp } from '../store/app'
import { addProjectFile, newProject } from '../pages/ProjectsPage'

// the end of a long path, where the folder's own name is
function tail(path: string, n = 30): string {
  return path.length > n ? '...' + path.slice(path.length - n + 3) : path
}

export default function ProjectPicker(): JSX.Element {
  const projects = useApp((s) => s.projects)
  const shown = useApp((s) => s.project)
  const busy = useApp((s) => s.busy)
  const [open, setOpen] = useState(false)
  const current = projects.find((p) => p.file === shown)

  useEffect(() => {
    if (!open) return
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [open])

  const pick = (file: string): void => {
    setOpen(false)
    useApp.getState().selectProject(file)
  }
  const then = (action: () => Promise<void>) => (): void => {
    setOpen(false)
    action()
  }

  return (
    <div className="px-3 pb-4">
      <div className="relative">
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() => setOpen(!open)}
          title={busy ? 'Another project can be picked when the running job is done' : current?.file}
          className="flex w-full items-center gap-2.5 rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-left transition-colors hover:border-ink-600 hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <FolderKanban size={16} className="shrink-0 text-blob-light" />
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] font-semibold uppercase tracking-wider text-ink-400">Project</span>
            <span className="block truncate text-sm font-medium text-slate-100">{current?.title ?? 'Default'}</span>
          </span>
          <ChevronsUpDown size={15} className="shrink-0 text-ink-400" />
        </button>
        {open && (
          <>
            <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
            <div className="absolute left-0 right-0 top-full z-40 mt-1 overflow-hidden rounded-lg border border-ink-600 bg-ink-850 shadow-xl shadow-black/50">
              <ul className="max-h-80 overflow-y-auto py-1">
                {projects.map((p) => {
                  const on = p.file === shown
                  return (
                    <li key={p.file}>
                      <button
                        type="button"
                        disabled={p.missing}
                        onClick={() => pick(p.file)}
                        title={p.file}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-ink-700/60 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Check size={14} className={`shrink-0 ${on ? 'text-delta' : 'invisible'}`} />
                        <span className="min-w-0 flex-1">
                          <span className={`block truncate text-sm ${on ? 'font-medium text-slate-100' : 'text-ink-300'}`}>{p.title}</span>
                          <span className="block truncate text-[11px] text-ink-400">
                            {p.missing ? 'its file is gone' : tail(p.install_dir || p.publish_src) || 'not set up yet'}
                          </span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
              <div className="border-t border-ink-700 py-1">
                <button type="button" onClick={then(newProject)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink-300 transition-colors hover:bg-ink-700/60 hover:text-slate-100">
                  <Plus size={14} className="shrink-0 text-delta-light" />
                  New project...
                </button>
                <button type="button" onClick={then(addProjectFile)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink-300 transition-colors hover:bg-ink-700/60 hover:text-slate-100">
                  <FilePlus size={14} className="shrink-0 text-delta-light" />
                  Add a project file...
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
