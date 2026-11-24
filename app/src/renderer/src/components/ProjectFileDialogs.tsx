// The dialogs of project files (.deltapatch, .html). Import: what a file holds and who signed it, a tick and
// a folder for each project. Export: which projects go into one, which kind of file, signed or not.
import { FileDown, FolderOpen, PackagePlus, ShieldAlert, ShieldCheck } from 'lucide-react'
import { Exporting, ImportEntry, Importing, useApp } from '../store/app'
import { baseName, finishExport, finishImport, nameProblem } from '../store/projectActions'
import { suggestedFolder } from '../store/folders'
import { Frame } from './Modals'
import { Button, Toggle } from './ui'

function Tick(props: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }): JSX.Element {
  return (
    <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 cursor-pointer accent-delta disabled:cursor-not-allowed"
      checked={props.checked} disabled={props.disabled} onChange={(e) => props.onChange(e.target.checked)} />
  )
}

export function ImportDialog(): JSX.Element | null {
  const im = useApp((s) => s.importing)
  const projects = useApp((s) => s.projects)
  if (!im) return null
  const set = (change: Partial<Importing>): void => useApp.setState({ importing: { ...im, ...change } })
  const setEntry = (i: number, change: Partial<ImportEntry>): void =>
    set({ entries: im.entries.map((e, j) => (j === i ? { ...e, ...change } : e)) })
  const picked = im.entries.filter((e) => e.pick)
  const problemOf = (e: ImportEntry): string =>
    nameProblem(e.name, [...projects.map((p) => p.title), ...picked.filter((x) => x !== e).map((x) => x.name)])
  const ready = picked.length > 0 && picked.every((e) => !problemOf(e)) && !im.working
  const browse = async (i: number): Promise<void> => {
    const folder = await window.api.pickFolder(im.entries[i].folder)
    if (folder) setEntry(i, { folder: suggestedFolder(folder, im.entries[i].install_folder ?? '') })
  }

  return (
    <Frame wide title={`Add projects from ${baseName(im.file)}`} icon={<PackagePlus size={18} className="text-delta" />}
      footer={
        <>
          <Button variant="ghost" disabled={im.working} onClick={() => useApp.setState({ importing: null })}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={finishImport}>
            {im.working ? 'Adding...' : picked.length === 1 ? 'Add 1 project' : `Add ${picked.length} projects`}
          </Button>
        </>
      }>
      {im.signed ? (
        <div className="flex items-start gap-3 rounded-lg border border-delta/30 bg-delta/10 px-4 py-3 text-delta-light">
          <ShieldCheck size={18} className="mt-0.5 shrink-0" />
          <p className="whitespace-pre-line text-sm">
            {`Signed by the publisher key ${im.fingerprint}.\nNothing in it was changed after it was signed.`}
          </p>
        </div>
      ) : (
        <div className="flex items-start gap-3 rounded-lg border border-blob/30 bg-blob/10 px-4 py-3 text-blob-light">
          <ShieldAlert size={18} className="mt-0.5 shrink-0" />
          <p className="whitespace-pre-line text-sm">
            {'Not signed: anyone could have made or changed this file.\nAdd it only if you trust where it came from.'}
          </p>
        </div>
      )}
      <div className="mt-4 max-h-[46vh] space-y-3 overflow-y-auto pr-1">
        {im.entries.map((e, i) => {
          const problem = e.pick ? problemOf(e) : ''
          return (
            <div key={e.title} className={`rounded-lg border px-4 py-3 ${e.pick ? 'border-ink-600 bg-ink-900/70' : 'border-ink-700 opacity-70'}`}>
              <label className="flex cursor-pointer items-start gap-3">
                <Tick checked={e.pick} disabled={im.working} onChange={(v) => setEntry(i, { pick: v })} />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-slate-100">{e.title}</span>
                  {e.description && <span className="block text-xs text-ink-300">{e.description}</span>}
                  <span className="selectable block break-all font-mono text-[12px] text-ink-300">{e.source_url}</span>
                  <span className="block text-[11px] text-ink-400">
                    {e.trust_key ? 'Its releases must be signed by the publisher key it gives.' : 'No publisher key given.'}
                  </span>
                </span>
              </label>
              {e.pick && (
                <div className="mt-3 space-y-2 pl-7">
                  {(e.taken || e.name !== e.title) && (
                    <div>
                      <label className="label">Its name here</label>
                      <input className="input" value={e.name} spellCheck={false} disabled={im.working}
                        onChange={(ev) => setEntry(i, { name: ev.target.value })} />
                      {e.taken && <p className="hint">{`There is a project named ${e.title} already.\nThis one gets another name.`}</p>}
                    </div>
                  )}
                  <div>
                    {e.install_hint && <p className="mb-2 whitespace-pre-line text-sm text-slate-200">{e.install_hint}</p>}
                    <label className="label">The folder it keeps up to date</label>
                    <div className="flex gap-2">
                      <input className="input font-mono text-[13px]" value={e.folder} spellCheck={false} disabled={im.working}
                        placeholder="Empty: choose it later on the Update page" onChange={(ev) => setEntry(i, { folder: ev.target.value })} />
                      <Button icon={FolderOpen} disabled={im.working} onClick={() => browse(i)}>Browse</Button>
                    </div>
                    {e.install_folder && <p className="hint">{`Browse puts it in ${e.install_folder} inside the folder you pick.`}</p>}
                  </div>
                  {problem && <p className="text-xs text-rose-300">{problem}</p>}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="mt-4">
        <Toggle checked={im.updateNow} onChange={(v) => set({ updateNow: v })} label="Update them now"
          hint={'The projects with a folder are updated once they are added.\nOnly what is missing or different is downloaded.'} />
      </div>
    </Frame>
  )
}

const KINDS: { value: Exporting['kind']; label: string; hint: string }[] = [
  { value: 'deltapatch', label: '.deltapatch', hint: 'Opens in DeltaPatcher with a double-click.' },
  { value: 'html', label: '.html', hint: 'A page anyone can read in a browser.\nPlayers drag it onto DeltaPatcher.' }
]

export function ExportDialog(): JSX.Element | null {
  const ex = useApp((s) => s.exporting)
  const projects = useApp((s) => s.projects)
  if (!ex) return null
  const set = (change: Partial<Exporting>): void => useApp.setState({ exporting: { ...ex, ...change } })
  const chosen = projects.filter((p) => ex.picks[p.file])
  const ready = chosen.length > 0 && chosen.every((p) => p.address)

  return (
    <Frame wide title="Export projects" icon={<FileDown size={18} className="text-delta" />}
      footer={
        <>
          <Button variant="ghost" onClick={() => useApp.setState({ exporting: null })}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={finishExport}>Export...</Button>
        </>
      }>
      <p className="whitespace-pre-line">
        {'A project file gives other people your projects: where each one updates from, its publisher key, its description and website.\nNever a folder, a server login or a key file.'}
      </p>
      <label className="label mt-5">Projects in it</label>
      <div className="max-h-52 divide-y divide-ink-700/70 overflow-y-auto rounded-lg border border-ink-600 bg-ink-900">
        {projects.map((p) => (
          <label key={p.file} className={`flex items-start gap-3 px-3 py-2.5 ${p.address ? 'cursor-pointer' : 'opacity-60'}`}>
            <Tick checked={Boolean(ex.picks[p.file])} disabled={!p.address}
              onChange={(v) => set({ picks: { ...ex.picks, [p.file]: v } })} />
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-slate-100">{p.title}</span>
              <span className="block break-all font-mono text-[11px] text-ink-400">
                {p.address || 'No http(s) address players update from: set public_url on its Release page.'}
              </span>
            </span>
          </label>
        ))}
      </div>
      <label className="label mt-5">File type</label>
      <div className="grid gap-2 md:grid-cols-2">
        {KINDS.map((k) => (
          <label key={k.value} className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
            ex.kind === k.value ? 'border-delta/50 bg-delta/10' : 'border-ink-600 hover:border-ink-500'}`}>
            <input type="radio" name="kind" className="mt-1 accent-delta" checked={ex.kind === k.value} onChange={() => set({ kind: k.value })} />
            <span>
              <span className="block font-mono text-sm text-slate-100">{k.label}</span>
              <span className="block whitespace-pre-line text-xs text-ink-300">{k.hint}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="mt-5">
        {ex.keyExists ? (
          <Toggle checked={ex.sign} onChange={(v) => set({ sign: v })} label="Sign it with your signing key"
            hint={`${ex.key}\nIts passphrase is asked when it has one, and never kept.`} />
        ) : (
          <p className="hint">{'It is not signed: there is no signing key yet.\nMake one on the Release page, under Sign releases.'}</p>
        )}
      </div>
    </Frame>
  )
}
