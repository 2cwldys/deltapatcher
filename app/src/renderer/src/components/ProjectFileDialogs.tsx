// The dialogs of project files (.deltapatch, .html). Import: what a file holds and who signed it, a tick and
// a folder for each project; a project of an update capable file is marked so, and one this computer has
// already is updated in place (what changes listed), or added as a new project instead. Export: which projects
// go into one, update capable or a new project for every file, which kind of file, signed or not.
import { FileDown, FolderOpen, PackagePlus, RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-react'
import { Exporting, ImportEntry, Importing, useApp } from '../store/app'
import { baseName, finishExport, finishImport, nameProblem } from '../store/projectActions'
import { suggestedFolder } from '../store/folders'
import { changeLines, importButton, inPlace, UPDATE_CAPABLE, updateLine } from '../store/publisher'
import { Frame } from './Modals'
import { Button, InfoTip, Toggle } from './ui'

function Tick(props: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }): JSX.Element {
  return (
    <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 cursor-pointer accent-delta disabled:cursor-not-allowed"
      checked={props.checked} disabled={props.disabled} onChange={(e) => props.onChange(e.target.checked)} />
  )
}

// the mark beside a project of an update capable file; its hover text says what that means
export function UpdateCapable(): JSX.Element {
  return (
    <span title={UPDATE_CAPABLE} aria-label={UPDATE_CAPABLE}
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-delta/30 bg-delta/10 px-2 py-0.5 text-[11px] font-medium text-delta-light">
      <RefreshCw size={11} />
      update capable
    </span>
  )
}

// a project the file updates here: what it updates and what changes, or a new project instead
function InPlace(props: { e: ImportEntry; working: boolean; onAsNew: () => void }): JSX.Element {
  const { e } = props
  const c = e.change
  return (
    <div className="mt-3 space-y-2 pl-7">
      <p className="whitespace-pre-line text-sm text-slate-200">{updateLine(e)}</p>
      {c && (
        <div className="selectable space-y-0.5 rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-[12px] text-ink-300">
          {changeLines(c).map((line) => <div key={line} className="break-all">{line}</div>)}
        </div>
      )}
      {c?.warning && (
        <p className={`whitespace-pre-line text-xs ${c.trust === 'signed' ? 'text-ink-300' : 'text-blob-light'}`}>{c.warning}</p>
      )}
      <p className="hint">{'Its folder, its name here and your own settings stay as they are.'}</p>
      <Button small variant="ghost" disabled={props.working} onClick={props.onAsNew}>Add it as a new project instead</Button>
    </div>
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
  const adding = picked.filter((e) => !inPlace(e))
  const problemOf = (e: ImportEntry): string =>
    nameProblem(e.name, [...projects.map((p) => p.title), ...adding.filter((x) => x !== e).map((x) => x.name)])
  const ready = picked.length > 0 && adding.every((e) => !problemOf(e)) && !im.working
  const browse = async (i: number): Promise<void> => {
    const folder = await window.api.pickFolder(im.entries[i].folder)
    if (folder) setEntry(i, { folder: suggestedFolder(folder, im.entries[i].install_folder ?? '') })
  }

  return (
    <Frame wide title={`Add projects from ${baseName(im.file)}`} icon={<PackagePlus size={18} className="text-delta" />}
      footer={
        <>
          <Button variant="ghost" disabled={im.working} onClick={() => useApp.setState({ importing: null })}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={finishImport}>{importButton(im.entries, im.working)}</Button>
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
          const problem = e.pick && !inPlace(e) ? problemOf(e) : ''
          return (
            <div key={e.title} className={`rounded-lg border px-4 py-3 ${e.pick ? 'border-ink-600 bg-ink-900/70' : 'border-ink-700 opacity-70'}`}>
              <label className="flex cursor-pointer items-start gap-3">
                <Tick checked={e.pick} disabled={im.working} onChange={(v) => setEntry(i, { pick: v })} />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-slate-100">{e.title}</span>
                    {im.updates && <UpdateCapable />}
                  </span>
                  {e.description && <span className="block text-xs text-ink-300">{e.description}</span>}
                  <span className="selectable block break-all font-mono text-[12px] text-ink-300">{e.source_url}</span>
                  <span className="block text-[11px] text-ink-400">
                    {e.trust_key ? 'Its releases must be signed by the publisher key it gives.' : 'No publisher key given.'}
                  </span>
                </span>
              </label>
              {e.pick && inPlace(e) && <InPlace e={e} working={im.working} onAsNew={() => setEntry(i, { asNew: true })} />}
              {e.pick && !inPlace(e) && (
                <div className="mt-3 space-y-2 pl-7">
                  {e.updates && (
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm text-slate-200">{`A new project, beside ${e.updates.project}.`}</p>
                      <Button small variant="ghost" disabled={im.working} onClick={() => setEntry(i, { asNew: false })}>
                        {`Update ${e.updates.project} instead`}
                      </Button>
                    </div>
                  )}
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
          hint={'The projects added with a folder are updated once they are added.\nOnly what is missing or different is downloaded.'} />
      </div>
    </Frame>
  )
}

const KINDS: { value: Exporting['kind']; label: string; hint: string }[] = [
  { value: 'deltapatch', label: '.deltapatch', hint: 'Opens in DeltaPatcher with a double-click.' },
  { value: 'html', label: '.html', hint: 'A page anyone can read in a browser.\nPlayers drag it onto DeltaPatcher.' }
]

const UPDATES: { value: boolean; label: string; hint: string }[] = [
  { value: true, label: 'Update capable', hint: 'Players are asked when you change this project later.' },
  { value: false, label: 'A new project for every file', hint: 'Each player who opens it gets a project of their own, as it is now.' }
]

function Choice<T>(props: { name: string; options: { value: T; label: string; hint: string }[]; value: T; onChange: (v: T) => void; mono?: boolean }): JSX.Element {
  return (
    <div className="grid gap-2 md:grid-cols-2">
      {props.options.map((k) => (
        <label key={k.label} className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
          props.value === k.value ? 'border-delta/50 bg-delta/10' : 'border-ink-600 hover:border-ink-500'}`}>
          <input type="radio" name={props.name} className="mt-1 accent-delta" checked={props.value === k.value} onChange={() => props.onChange(k.value)} />
          <span>
            <span className={`block text-sm text-slate-100 ${props.mono ? 'font-mono' : ''}`}>{k.label}</span>
            <span className="block whitespace-pre-line text-xs text-ink-300">{k.hint}</span>
          </span>
        </label>
      ))}
    </div>
  )
}

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
      <div className="mt-5 mb-1.5 flex items-center gap-2">
        <label className="label !mb-0">Later changes</label>
        <InfoTip text={'Update capable: when you change a project (its description, website, where it goes, its address or key), players who have it are asked before anything changes; opening a newer file updates their project.\n' +
          'A new project for every file: opening it always adds a project, as before, and players are not asked about later changes.\n' +
          'An update capable file needs this DeltaPatcher or a newer one; an older one asks to be updated first.'} />
      </div>
      <Choice name="updates" options={UPDATES} value={ex.updates} onChange={(v) => set({ updates: v })} />
      <label className="label mt-5">File type</label>
      <Choice name="kind" options={KINDS} value={ex.kind} onChange={(v) => set({ kind: v })} mono />
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
