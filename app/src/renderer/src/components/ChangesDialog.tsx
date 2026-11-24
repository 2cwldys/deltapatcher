// What changed: a release's notes from its publisher, the files it adds, changes and removes (and the player's
// own copies it keeps), and its whole file list, with a search box. File names show only when the release's
// publisher shows them (show_files); otherwise the counts say how many.
import { useState } from 'react'
import { FileDiff, Search, X } from 'lucide-react'
import { useApp } from '../store/app'
import { changeLists, fileList } from '../store/publisher'
import { Frame } from './Modals'
import { Button, Segmented, human } from './ui'

type Tab = 'changed' | 'all'

export default function ChangesDialog(): JSX.Element | null {
  const view = useApp((s) => s.changesView)
  const [tab, setTab] = useState<Tab>('changed')
  const [search, setSearch] = useState('')
  if (!view) return null
  const r = view.result
  const close = (): void => {
    setSearch('')
    setTab('changed')
    useApp.setState({ changesView: null })
  }
  const lists = changeLists(r, search)
  const all = fileList(r, search)
  const release = r.release && (r.release.name || r.release.version) ? `${r.release.name} ${r.release.version}`.trim() : ''
  const counts = `${r.add} new, ${r.replace} changed, ${r.remove} removed${r.kept?.length ? `, ${r.kept.length} kept` : ''}`
  const more = (shown: number, matched: number): string | null =>
    matched > shown ? `and ${matched - shown} more: type in the search box to find one` : null

  return (
    <Frame wide title={`What changed: ${view.title}`} icon={<FileDiff size={18} className="text-delta" />}
      footer={<Button variant="primary" icon={X} onClick={close}>Close</Button>}>
      <p className="text-sm text-ink-300">{`${release ? `${release}: ` : ''}${counts}.`}</p>
      {r.notes && (
        <div className="mt-3">
          <p className="label">From the publisher</p>
          <div className="selectable max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 text-sm text-slate-200">
            {r.notes}
          </div>
        </div>
      )}
      {!r.changes ? (
        <p className="mt-4 whitespace-pre-line text-sm text-ink-400">
          {"This release's publisher does not show file names.\nThe counts above say how many files change."}
        </p>
      ) : (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-3">
            <Segmented value={tab} onChange={setTab}
              options={[{ value: 'changed', label: 'What changed' }, { value: 'all', label: 'What is in it' }]} />
            <div className="relative min-w-[12rem] flex-1">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
              <input className="input pl-8" placeholder="Search the file names" value={search} spellCheck={false}
                onChange={(e) => setSearch(e.target.value)} />
            </div>
          </div>
          <div className="mt-3 max-h-[40vh] overflow-y-auto pr-1">
            {tab === 'changed' ? (
              lists.filter((l) => l.total > 0).length === 0 ? (
                <p className="text-sm text-ink-400">No file changes: the folder has the release already.</p>
              ) : (
                lists.filter((l) => l.total > 0).map((l) => (
                  <div key={l.key} className="mb-3">
                    <p className="label">{`${l.label} (${search.trim() ? `${l.matched} of ${l.total}` : l.total})`}</p>
                    <div className="selectable space-y-0.5 font-mono text-[12px] text-ink-300">
                      {l.names.map((n) => <div key={n} className="break-all">{n}</div>)}
                      {more(l.names.length, l.matched) && <div className="font-sans text-xs text-ink-400">{more(l.names.length, l.matched)}</div>}
                    </div>
                  </div>
                ))
              )
            ) : all ? (
              <div>
                <p className="label">{`Every file of the release (${search.trim() ? `${all.matched} of ${all.total}` : all.total})`}</p>
                <div className="selectable space-y-0.5 font-mono text-[12px] text-ink-300">
                  {all.files.map((f) => (
                    <div key={f.path} className="flex gap-3">
                      <span className="min-w-0 flex-1 break-all">{f.path}</span>
                      <span className="shrink-0 tabular-nums text-ink-400">{human(f.size)}</span>
                    </div>
                  ))}
                  {more(all.files.length, all.matched) && <div className="font-sans text-xs text-ink-400">{more(all.files.length, all.matched)}</div>}
                </div>
              </div>
            ) : (
              <p className="text-sm text-ink-400">Check the project again to see its whole list.</p>
            )}
          </div>
        </div>
      )}
    </Frame>
  )
}
