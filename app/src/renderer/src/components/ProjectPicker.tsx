// The project picker at the top of the sidebar: the project Update, Release and Settings show, and a list to
// pick another (it scrolls, and a search box finds one in a long list), rename or remove one, make a new one,
// or add a project file.
import { useEffect, useState } from 'react'
import { Check, ChevronsUpDown, FilePlus, FolderKanban, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { ProjectInfo, useApp } from '../store/app'
import { newProject, openProjectFile, removeProject, renameProject } from '../store/projectActions'

// more projects than this get a search box
const SEARCH = 8

// the end of a long path, where the folder's own name is
function tail(path: string, n = 30): string {
  return path.length > n ? '...' + path.slice(path.length - n + 3) : path
}

export default function ProjectPicker(): JSX.Element {
  const projects = useApp((s) => s.projects)
  const shown = useApp((s) => s.project)
  const busy = useApp((s) => s.busy)
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const current = projects.find((p) => p.file === shown)
  const words = search.trim().toLowerCase()
  const listed = words ? projects.filter((p) => p.title.toLowerCase().includes(words)) : projects

  useEffect(() => {
    if (!open) {
      setSearch('')
      return
    }
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
  const then = (action: () => Promise<unknown>) => (): void => {
    setOpen(false)
    action()
  }
  const onRow = (e: React.MouseEvent, action: (p: ProjectInfo) => Promise<unknown>, p: ProjectInfo): void => {
    e.stopPropagation()
    setOpen(false)
    action(p)
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
            <div className="absolute left-0 top-full z-40 mt-1 w-72 overflow-hidden rounded-lg border border-ink-600 bg-ink-850 shadow-xl shadow-black/50">
              {projects.length > SEARCH && (
                <div className="relative border-b border-ink-700 p-2">
                  <Search size={14} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-400" />
                  <input autoFocus className="input py-1.5 pl-8 text-[13px]" placeholder="Search projects" value={search}
                    spellCheck={false} onChange={(e) => setSearch(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && listed[0] && !listed[0].missing && pick(listed[0].file)} />
                </div>
              )}
              <ul className="max-h-80 overflow-y-auto py-1">
                {listed.map((p) => {
                  const on = p.file === shown
                  return (
                    <li key={p.file} className="group relative">
                      <button
                        type="button"
                        disabled={p.missing}
                        onClick={() => pick(p.file)}
                        title={p.file}
                        className="flex w-full items-center gap-2 py-2 pl-3 pr-16 text-left transition-colors hover:bg-ink-700/60 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Check size={14} className={`shrink-0 ${on ? 'text-delta' : 'invisible'}`} />
                        <span className="min-w-0 flex-1">
                          <span className={`block truncate text-sm ${on ? 'font-medium text-slate-100' : 'text-ink-300'}`}>{p.title}</span>
                          <span className="block truncate text-[11px] text-ink-400">
                            {p.missing ? 'its file is gone' : tail(p.install_dir || p.publish_src) || 'not set up yet'}
                          </span>
                        </span>
                      </button>
                      <div className="absolute right-2 top-1/2 hidden -translate-y-1/2 gap-0.5 group-focus-within:flex group-hover:flex">
                        {!p.missing && (
                          <button type="button" title={`Rename ${p.title}`} onClick={(e) => onRow(e, renameProject, p)}
                            className="rounded p-1.5 text-ink-400 hover:bg-ink-600 hover:text-slate-100">
                            <Pencil size={13} />
                          </button>
                        )}
                        {p.kind !== 'main' && (
                          <button type="button" title={`Remove ${p.title}`} onClick={(e) => onRow(e, removeProject, p)}
                            className="rounded p-1.5 text-ink-400 hover:bg-rose-500/15 hover:text-rose-300">
                            <Trash2 size={13} />
                          </button>
                        )}
                      </div>
                    </li>
                  )
                })}
                {words && !listed.length && <li className="px-3 py-2 text-xs text-ink-400">No project matches.</li>}
              </ul>
              <div className="border-t border-ink-700 py-1">
                <button type="button" onClick={then(newProject)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink-300 transition-colors hover:bg-ink-700/60 hover:text-slate-100">
                  <Plus size={14} className="shrink-0 text-delta-light" />
                  New project...
                </button>
                <button type="button" onClick={then(openProjectFile)}
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
