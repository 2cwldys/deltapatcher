// A list of files and folders inside the folder being released, picked with Add folder / Add file and
// kept in one setting, separated by ; (paths from the top of that folder; a folder ends with /). A
// pattern typed into deltapatcher.ini (*.log) shows here too, and can be removed.
import { ReactNode } from 'react'
import { Asterisk, File, FilePlus, Folder, FolderPlus, X } from 'lucide-react'
import { useApp } from '../store/app'
import { ask } from '../store/modal'
import { Button } from './ui'

export function listOf(value: unknown): string[] {
  return String(value ?? '')
    .split(';')
    .map((x) => x.trim())
    .filter(Boolean)
}

// the path inside `root`, with forward slashes: '' for root itself, null when it is not inside
function inside(root: string, picked: string): string | null {
  const norm = (s: string): string => s.replace(/\\/g, '/').replace(/\/+$/, '')
  const r = norm(root)
  const p = norm(picked)
  if (p.toLowerCase() === r.toLowerCase()) return ''
  if (!p.toLowerCase().startsWith(r.toLowerCase() + '/')) return null
  return p.slice(r.length + 1)
}

export function PathList(props: { setting: string; label: string; hint: ReactNode; empty: string }): JSX.Element {
  const items = useApp((s) => listOf(s.settings[props.setting]))
  const root = useApp((s) => String(s.settings.publish_src ?? ''))
  const set = useApp((s) => s.set)
  const save = (next: string[]): void => set(props.setting, next.join('; '))

  const add = async (kind: 'folder' | 'file'): Promise<void> => {
    if (!root) {
      await ask.info('Choose the folder to release first', 'Files and folders are picked from inside it.')
      return
    }
    const picked = kind === 'folder' ? await window.api.pickFolder(root) : await window.api.pickFile(root)
    if (!picked) return
    const rel = inside(root, picked)
    if (rel === null) {
      await ask.info('Pick something inside the folder to release', `${picked}\n\nis not inside\n\n${root}`)
      return
    }
    if (rel === '') {
      await ask.info('That is the whole folder', 'Pick a file or a folder inside it.')
      return
    }
    const entry = kind === 'folder' ? rel + '/' : rel
    if (!items.some((x) => x.toLowerCase() === entry.toLowerCase())) save([...items, entry])
  }

  return (
    <div>
      <label className="label">{props.label}</label>
      <div className="rounded-lg border border-ink-600 bg-ink-900">
        {items.length === 0 ? (
          <p className="px-3 py-2.5 text-sm text-ink-400">{props.empty}</p>
        ) : (
          <ul className="divide-y divide-ink-700/70">
            {items.map((item) => {
              const pattern = /[*?[]/.test(item)
              const folder = !pattern && item.endsWith('/')
              const Icon = pattern ? Asterisk : folder ? Folder : File
              return (
                <li key={item} className="flex items-center gap-2.5 px-3 py-2">
                  <Icon size={15} className={folder ? 'shrink-0 text-blob-light' : 'shrink-0 text-ink-300'} />
                  <span className="selectable min-w-0 flex-1 break-all font-mono text-[13px] text-slate-100">
                    {folder ? item.slice(0, -1) : item}
                  </span>
                  <span className="shrink-0 text-xs text-ink-400">{pattern ? 'pattern' : folder ? 'folder' : 'file'}</span>
                  <button
                    type="button"
                    title="Remove"
                    onClick={() => save(items.filter((x) => x !== item))}
                    className="shrink-0 rounded p-1 text-ink-400 hover:bg-ink-700 hover:text-slate-100"
                  >
                    <X size={14} />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button small icon={FolderPlus} onClick={() => add('folder')}>
          Add folder
        </Button>
        <Button small icon={FilePlus} onClick={() => add('file')}>
          Add file
        </Button>
      </div>
      <p className="hint">{props.hint}</p>
    </div>
  )
}
