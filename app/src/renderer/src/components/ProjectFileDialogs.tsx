// The dialogs of project files (.deltapatch, .html). Import: what a file holds and who signed it, a tick and
// a folder for each project; a project of an update capable file is marked so, and one this computer has
// already is updated in place (what changes listed), or added as a new project instead. A project shared by
// torrent is marked so, with the choice to seed it (off at first), or what refused torrents leave of it. A
// publisher's private file sets up releasing instead: this computer's folders and keys, the server's host keys to
// trust, or a project here updated in place; a player adds it as a player. Export: public (for players) or private
// (for the publisher, on any computer) first, then which projects go into one, update capable or a new project for
// every file, which kind of file, signed or not.
import { FileDown, FileSearch, FolderOpen, KeyRound, Lock, Magnet, PackagePlus, RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-react'
import { Exporting, ImportEntry, Importing, useApp } from '../store/app'
import { ask } from '../store/modal'
import { baseName, finishExport, finishImport, nameProblem, sentences, startImport } from '../store/projectActions'
import { suggestedFolder } from '../store/folders'
import { changeLines, importButton, inPlace, shown, UPDATE_CAPABLE, updateLine } from '../store/publisher'
import {
  APPLY_OLDER, EXPORT_TYPES, exportChosen, exportProblem, hostKeysLine, inPlaceLine, KEY_BY_HAND, keyUnknownLine, leaveIt,
  picksFor, playerLine, PRIVATE_EXPORT, PRIVATE_IMPORT, privateImportButton, privateRuns
} from '../store/privateFiles'
import { refusedLine, SEED_IT, torrentMark } from '../store/seeding'
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

// the mark of a project shared by torrent (by torrent only: it has no address); its hover text says what that means
export function TorrentMark(props: { only: boolean }): JSX.Element {
  const mark = torrentMark(props.only)
  return (
    <span title={mark.hint} aria-label={mark.hint}
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[11px] font-medium text-sky-300">
      <Magnet size={11} />
      {mark.text}
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

// a folder or a file of this computer, typed or picked
function PathInput(props: { label: string; value: string; onChange: (v: string) => void; browse: 'folder' | 'file'; disabled: boolean;
  placeholder?: string; line?: string; extra?: JSX.Element }): JSX.Element {
  const pick = async (): Promise<void> => {
    const p = props.browse === 'file' ? await window.api.pickFile(props.value) : await window.api.pickFolder(props.value)
    if (p) props.onChange(p)
  }
  return (
    <div>
      <label className="label">{props.label}</label>
      <div className="flex gap-2">
        <input className="input font-mono text-[13px]" value={props.value} spellCheck={false} disabled={props.disabled}
          placeholder={props.placeholder} onChange={(ev) => props.onChange(ev.target.value)} />
        <Button icon={props.browse === 'file' ? FileSearch : FolderOpen} disabled={props.disabled} onClick={pick}>Browse</Button>
        {props.extra}
      </div>
      {props.line && <p className="hint">{props.line}</p>}
    </div>
  )
}

// host keys as fingerprints, one a line
function KeyList(props: { keys: { type: string; fingerprint: string }[] }): JSX.Element {
  return (
    <div className="space-y-1.5">
      {props.keys.map((k) => (
        <div key={k.fingerprint} className="selectable flex gap-3 rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-xs">
          <span className="w-28 shrink-0 text-ink-400">{k.type}</span>
          <span className="break-all text-violet-300">{k.fingerprint}</span>
        </div>
      ))}
    </div>
  )
}

// the server's host keys a private file gives, with their fingerprints; added to this computer's known_hosts only
// when the person says they are theirs, and only while this computer trusts no key for that server (when it trusts
// others, both are shown and nothing is added)
function HostKeysPart(props: { e: ImportEntry; working: boolean; onTrust: (v: boolean) => void }): JSX.Element | null {
  const h = props.e.host_keys
  if (!h || !h.state) return null
  return (
    <div className="mt-3 space-y-2 pl-7">
      <div>
        <label className="label">The server's host keys</label>
        <p className="selectable font-mono text-[12px] text-ink-300">{h.host}</p>
      </div>
      <KeyList keys={h.keys} />
      {h.state === 'other' && (
        <>
          <p className="text-xs text-ink-300">The host keys this computer trusts for it:</p>
          <KeyList keys={h.here ?? []} />
        </>
      )}
      {h.state === 'new' ? (
        <Toggle checked={props.e.trustHostKeys} disabled={props.working} onChange={props.onTrust}
          label="These are my server's host keys: trust them" line={hostKeysLine(h)} />
      ) : (
        <p className={`hint ${h.state === 'other' ? 'text-blob-light' : ''}`}>{hostKeysLine(h)}</p>
      )}
    </div>
  )
}

// a project this computer releases with the file's own key: what the file changes in it, or a new project instead.
// A file older than the one applied to it shows both times and leaves it as it is, unless the person says Apply it
// anyway
function PrivateInPlace(props: { e: ImportEntry; working: boolean; onAsNew: () => void; onApplyOlder: (v: boolean) => void }): JSX.Element {
  const { e } = props
  const name = e.updates?.project ?? e.title
  const changes = e.changes ?? []
  return (
    <div className="mt-3 space-y-2 pl-7">
      <p className={`whitespace-pre-line text-sm ${e.older ? 'text-blob-light' : 'text-slate-200'}`}>{inPlaceLine(name, e)}</p>
      {changes.length > 0 && (
        <div className="selectable space-y-0.5 rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-[12px] text-ink-300">
          {changes.map((c) => <div key={c.key} className="break-all">{`${c.label}: ${shown(c.from)} -> ${shown(c.to)}`}</div>)}
        </div>
      )}
      {e.older && (
        <Button small variant="ghost" disabled={props.working} onClick={() => props.onApplyOlder(!e.applyOlder)}>
          {e.applyOlder ? leaveIt(name) : APPLY_OLDER}
        </Button>
      )}
      <p className="hint">{'Its folders, its keys and whether it seeds stay as they are here.'}</p>
      <Button small variant="ghost" disabled={props.working} onClick={props.onAsNew}>Set it up as a new project instead</Button>
    </div>
  )
}

// A publisher's private file: it sets up releasing its projects from this computer. It shows the signing key it
// is signed with, and asks for this computer's own folders and keys; a player adds it as a player instead
function PrivateImportDialog(props: { im: Importing }): JSX.Element {
  const { im } = props
  const projects = useApp((s) => s.projects)
  const set = (change: Partial<Importing>): void => useApp.setState({ importing: { ...im, ...change } })
  const setEntry = (i: number, change: Partial<ImportEntry>): void =>
    set({ entries: im.entries.map((e, j) => (j === i ? { ...e, ...change } : e)) })
  const picked = im.entries.filter((e) => e.pick)
  const adding = picked.filter((e) => !inPlace(e))
  const problemOf = (e: ImportEntry): string =>
    nameProblem(e.name, [...projects.map((p) => p.title), ...adding.filter((x) => x !== e).map((x) => x.name)])
  const ready = privateRuns(im.entries).length > 0 && adding.every((e) => !problemOf(e)) && !im.working
  // a new SSH key at the path given: its passphrase asked twice, never kept; then the line for the server
  const makeKey = async (i: number): Promise<void> => {
    const path = im.entries[i].sshKey.trim()
    if (!path) return
    const p = await ask.passphrase('New SSH key', `A passphrase for the new key at\n${path}\n\nYou type it once per session (Unlock key). It is not saved anywhere.`, true, 10)
    if (p === null) return
    const r = await useApp.getState().side(['ssh', 'keygen', `--path=${path}`, '--passphrase-stdin'], p)
    if (!r.ok) return ask.info('No key made', sentences(String(r.message ?? '')))
    await window.api.copy(String(r.data.authorized_keys_line))
    await ask.info('Key made', 'Add this line to ~/.ssh/authorized_keys of the user on the server.\nIt allows SFTP only, nothing else.\nIt is on the clipboard.',
      String(r.data.authorized_keys_line))
  }

  return (
    <Frame wide title={`Set up releasing from ${baseName(im.file)}`} icon={<KeyRound size={18} className="text-delta" />}
      footer={
        <>
          <Button variant="ghost" disabled={im.working} onClick={() => void startImport(im.file, true)}>Add it as a player</Button>
          <Button variant="ghost" disabled={im.working} onClick={() => useApp.setState({ importing: null })}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={finishImport}>{privateImportButton(im.entries, im.working)}</Button>
        </>
      }>
      <div className="flex items-start gap-3 rounded-lg border border-delta/30 bg-delta/10 px-4 py-3 text-delta-light">
        <ShieldCheck size={18} className="mt-0.5 shrink-0" />
        <div className="min-w-0 text-sm">
          <p className="whitespace-pre-line">{`${PRIVATE_IMPORT}\nIt is signed with the signing key ${im.fingerprint}:`}</p>
          <p className="selectable mt-1 break-all font-mono text-[12px]">{im.signer}</p>
        </div>
      </div>
      <p className="hint">{`${KEY_BY_HAND}\nA player adds it with Add it as a player: only what a public file holds.`}</p>
      <div className="mt-4 max-h-[46vh] space-y-3 overflow-y-auto pr-1">
        {im.entries.map((e, i) => {
          const problem = e.pick && !inPlace(e) ? problemOf(e) : ''
          const mode = e.private?.deploy_mode
          return (
            <div key={e.title} className={`rounded-lg border px-4 py-3 ${e.pick ? 'border-ink-600 bg-ink-900/70' : 'border-ink-700 opacity-70'}`}>
              <label className="flex cursor-pointer items-start gap-3">
                <Tick checked={e.pick} disabled={im.working} onChange={(v) => setEntry(i, { pick: v })} />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-slate-100">{e.title}</span>
                    {e.torrent && <TorrentMark only={!e.source_url} />}
                  </span>
                  {e.description && <span className="block text-xs text-ink-300">{e.description}</span>}
                  {e.where && <span className="block text-xs text-ink-300">{`Releases to ${e.where}`}</span>}
                  {e.private?.public_url && (
                    <span className="selectable block break-all font-mono text-[12px] text-ink-300">{e.private.public_url}</span>
                  )}
                </span>
              </label>
              {e.pick && inPlace(e) && (
                <PrivateInPlace e={e} working={im.working} onAsNew={() => setEntry(i, { asNew: true })}
                  onApplyOlder={(v) => setEntry(i, { applyOlder: v })} />
              )}
              {e.pick && !inPlace(e) && (
                <div className="mt-3 space-y-3 pl-7">
                  {e.player_here && (
                    <p className="whitespace-pre-line text-sm text-blob-light">{playerLine(e.player_here.project)}</p>
                  )}
                  {e.key_unknown && (
                    <p className="whitespace-pre-line text-sm text-blob-light">{keyUnknownLine(e.key_unknown.project)}</p>
                  )}
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
                  {problem && <p className="text-xs text-rose-300">{problem}</p>}
                  <PathInput label="The folder to release" browse="folder" value={e.publishSrc} disabled={im.working}
                    placeholder="Empty: choose it later on the Release page" onChange={(v) => setEntry(i, { publishSrc: v })}
                    line="One source for it on every computer: a shared drive, or git." />
                  <PathInput label="Your signing key's file" browse="file" value={e.signingKey} disabled={im.working}
                    onChange={(v) => setEntry(i, { signingKey: v })}
                    line={"It must be the key this file is signed with.\nWith a passphrase, the first release checks it."} />
                  {mode === 'sftp' && (
                    <PathInput label="Your SSH key's file" browse="file" value={e.sshKey} disabled={im.working}
                      onChange={(v) => setEntry(i, { sshKey: v })} line="Read only by OpenSSH: DeltaPatcher never opens it."
                      extra={<Button icon={KeyRound} disabled={im.working || !e.sshKey.trim()} onClick={() => void makeKey(i)}>Make one</Button>} />
                  )}
                  {mode === 'folder' && (
                    <PathInput label="The folder releases are copied to" browse="folder" value={e.deployFolder} disabled={im.working}
                      placeholder="Empty: choose it later on the Release page" onChange={(v) => setEntry(i, { deployFolder: v })} />
                  )}
                  {e.torrent && !im.torrentsRefused && (
                    <Toggle checked={e.seed} disabled={im.working} onChange={(v) => setEntry(i, { seed: v })} label="Seed it from this computer" line={SEED_IT} />
                  )}
                </div>
              )}
              {e.pick && <HostKeysPart e={e} working={im.working} onTrust={(v) => setEntry(i, { trustHostKeys: v })} />}
            </div>
          )
        })}
      </div>
    </Frame>
  )
}

export function ImportDialog(): JSX.Element | null {
  const im = useApp((s) => s.importing)
  const projects = useApp((s) => s.projects)
  if (!im) return null
  if (im.private && !im.asPlayer) return <PrivateImportDialog im={im} />
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
      {im.asPlayer && (
        <p className="hint">{"It is a publisher's private file, for releasing.\nAdded as a player, it takes only what a public file holds."}</p>
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
                    {e.torrent && <TorrentMark only={!e.source_url} />}
                  </span>
                  {e.description && <span className="block text-xs text-ink-300">{e.description}</span>}
                  <span className="selectable block break-all font-mono text-[12px] text-ink-300">{e.source_url}</span>
                  <span className="block text-[11px] text-ink-400">
                    {e.trust_key ? 'Its releases must be signed by the publisher key it gives.' : 'No publisher key given.'}
                  </span>
                  {e.torrent && im.torrentsRefused && (
                    <span className="block text-[11px] text-blob-light">{refusedLine(Boolean(e.source_url))}</span>
                  )}
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
                  {e.torrent && !im.torrentsRefused && (
                    <div className="pt-1">
                      <Toggle checked={e.seed} disabled={im.working} onChange={(v) => setEntry(i, { seed: v })} label="Seed this project" line={SEED_IT} />
                    </div>
                  )}
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
  const ready = exportChosen(projects, ex.picks, ex.type).length > 0
  const priv = ex.type === 'private'

  return (
    <Frame wide title="Export projects" icon={<FileDown size={18} className="text-delta" />}
      footer={
        <>
          <Button variant="ghost" onClick={() => useApp.setState({ exporting: null })}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={finishExport}>Export...</Button>
        </>
      }>
      <label className="label">Type</label>
      <Choice name="type" options={EXPORT_TYPES} value={ex.type} onChange={(v) => set({ type: v, picks: picksFor(v, projects, ex.picks) })} />
      {priv ? (
        <div className="mt-4 flex items-start gap-3 rounded-lg border border-blob/30 bg-blob/10 px-4 py-3 text-blob-light">
          <Lock size={18} className="mt-0.5 shrink-0" />
          <p className="whitespace-pre-line text-sm">{PRIVATE_EXPORT}</p>
        </div>
      ) : (
        <p className="mt-4 whitespace-pre-line">
          {'A project file gives other people your projects: where each one updates from, its publisher key, its description and website.\nNever a folder, a server login or a key file.'}
        </p>
      )}
      <label className="label mt-5">Projects in it</label>
      <div className="max-h-52 divide-y divide-ink-700/70 overflow-y-auto rounded-lg border border-ink-600 bg-ink-900">
        {projects.map((p) => {
          const problem = exportProblem(p, ex.type)
          return (
            <label key={p.file} className={`flex items-start gap-3 px-3 py-2.5 ${problem ? 'opacity-60' : 'cursor-pointer'}`}>
              <Tick checked={Boolean(ex.picks[p.file]) && !problem} disabled={Boolean(problem)}
                onChange={(v) => set({ picks: { ...ex.picks, [p.file]: v } })} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-slate-100">{p.title}</span>
                <span className="block break-all font-mono text-[11px] text-ink-400">
                  {problem || p.address || 'By torrent only'}
                </span>
              </span>
            </label>
          )
        })}
      </div>
      {!priv && (
        <>
          <div className="mt-5 mb-1.5 flex items-center gap-2">
            <label className="label !mb-0">Later changes</label>
            <InfoTip text={'Update capable: when you change a project (its description, website, where it goes, its address or key), players who have it are asked before anything changes; opening a newer file updates their project.\n' +
              'A new project for every file: opening it always adds a project, as before, and players are not asked about later changes.\n' +
              'An update capable file needs this DeltaPatcher or a newer one; an older one asks to be updated first.'} />
          </div>
          <Choice name="updates" options={UPDATES} value={ex.updates} onChange={(v) => set({ updates: v })} />
        </>
      )}
      <label className="label mt-5">File type</label>
      <Choice name="kind" options={KINDS} value={ex.kind} onChange={(v) => set({ kind: v })} mono />
      <div className="mt-5">
        {priv ? (
          <p className="hint">{"It is signed with the projects' own signing key.\nIts passphrase is asked when it has one, and never kept."}</p>
        ) : ex.keyExists ? (
          <Toggle checked={ex.sign} onChange={(v) => set({ sign: v })} label="Sign it with your signing key"
            hint={`${ex.key}\nIts passphrase is asked when it has one, and never kept.`} />
        ) : (
          <p className="hint">{'It is not signed: there is no signing key yet.\nMake one on the Release page, under Sign releases.'}</p>
        )}
      </div>
    </Frame>
  )
}
