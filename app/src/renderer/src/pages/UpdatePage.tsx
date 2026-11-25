// The Update page: the folder a project keeps up to date and where from; Update, Check and Roll back; and what the
// last one found: the counts, the release notes and, when its publisher shows them, the files by name (What
// changed). A change its publisher made to the project itself waits here with Apply and Not now.
import { Download, FileDiff, Search, Undo2, ShieldCheck, ShieldAlert, FolderSync } from 'lucide-react'
import { ProjectRun, running, UpdateResult, useApp } from '../store/app'
import { ask } from '../store/modal'
import { showChanges } from '../store/projectActions'
import { hasWhatChanged, nameLines } from '../store/publisher'
import { rebuiltLine, toDownload } from '../store/download'
import { Banner, Button, Card, Chip, Field, Stat, human } from '../components/ui'
import PublisherPrompt from '../components/PublisherPrompt'

interface Result extends UpdateResult {
  check: boolean
}

function busyElsewhere(r: ProjectRun): string {
  const who = r.inAll ? (r.check ? 'Check all' : 'Update all') : 'The Projects page'
  const what = r.state === 'waiting' ? 'has this project in its queue' : r.state === 'checking' ? 'is checking this project' : 'is updating this project'
  return `${who} ${what}.\nUpdate, Check and Roll back come back here when it is done.`
}

export function insecure(url: string): boolean {
  const m = /^http:\/\/([^/:]+|\[[^\]]+\])/i.exec(url.trim())
  return Boolean(m) && !['localhost', '127.0.0.1', '[::1]'].includes(m![1].toLowerCase())
}

export async function runUpdate(check: boolean): Promise<Result | null> {
  const st = useApp.getState()
  const source = String(st.effective('source_url'))
  const folder = String(st.settings.install_dir || '')
  if (!folder) {
    await ask.info('Choose a folder', 'Choose the folder to keep up to date first.')
    return null
  }
  if (!check && insecure(source)) {
    const go = await ask.confirm(
      'Plain http to another machine',
      `${source} is not https.\n\nEvery file is still checked against the manifest's SHA-256, but the manifest itself could be changed on the way. Continue only on a network you trust, or when the release is signed and you set the publisher key.`,
      'Continue'
    )
    if (!go) return null
  }
  const args = ['update', folder, `--source=${source}`, '--files']
  const key = String(st.settings.trust_key || '').trim()
  if (key) args.push(`--trust-key=${key}`)
  if (check) args.push('--check')
  const project = st.project
  const r = await st.run(check ? 'Check' : 'Update', args, { cancellable: !check })
  if (!r.ok) return null
  const result = { ...(r.data as Result), check }
  if (useApp.getState().project === project) st.setResult('update', result)
  st.noteResult(project, r.data as UpdateResult, check)
  return result
}

export default function UpdatePage(): JSX.Element {
  const busy = useApp((s) => s.busy)
  const source = useApp((s) => String(s.settings.source_url || ''))
  const result = useApp((s) => s.results.update as Result | undefined)
  const project = useApp((s) => s.project)
  const title = useApp((s) => s.projects.find((p) => p.file === s.project)?.title ?? 'this project')
  // the Projects page is checking or updating this project (on its own, or in Update all)
  const elsewhere = useApp((s) => s.runs[s.project])
  const blocked = running(elsewhere)
  const stopped = Boolean(busy) || blocked

  const rollback = async (): Promise<void> => {
    const folder = String(useApp.getState().settings.install_dir || '')
    if (!folder) return
    if (!(await ask.confirm('Roll back', `Put back the files the last update replaced or removed in\n${folder}?`, 'Roll back'))) return
    const r = await useApp.getState().run('Roll back', ['rollback', folder])
    if (r.ok) useApp.getState().setResult('update', undefined)
  }

  return (
    <div className="space-y-5">
      <Card fold="update/folder" title="Update a folder" icon={FolderSync}>
        <div className="grid gap-5">
          <Field label="Source" setting="source_url" mono
            hint={"Where the release is: an http(s) address or a folder.\nThe game's maker gives you this."} />
          {insecure(source) && <Banner kind="warn">This address is plain <b>http</b> to another machine. Use https, or a signed release with the publisher key below.</Banner>}
          <Field label="Folder" setting="install_dir" browse="folder" placeholder="The game folder" mono
            hint={'The folder to bring up to date.\nFiles you added yourself are never removed.'} />
          <Field label="Publisher key (optional)" setting="trust_key" placeholder="ed25519:..." mono
            hint={"Paste the key the publisher gave you: only releases they signed are installed.\nEmpty: the first signed release's key is trusted, and only that key after it."} />
        </div>
        {blocked && elsewhere && (
          <div className="mt-5">
            <Banner kind="info"><span className="whitespace-pre-line">{busyElsewhere(elsewhere)}</span></Banner>
          </div>
        )}
        <div className="mt-6 flex flex-wrap gap-2">
          <Button variant="primary" icon={Download} disabled={stopped} onClick={() => runUpdate(false)}>Update</Button>
          <Button icon={Search} disabled={stopped} onClick={() => runUpdate(true)}>Check</Button>
          <Button variant="ghost" icon={Undo2} disabled={stopped} onClick={rollback}>Roll back</Button>
        </div>
      </Card>

      {result?.publisher_change && !blocked && <PublisherPrompt file={project} change={result.publisher_change} />}

      {result && (
        <Card fold="update/result" title={result.empty ? 'Up to date' : result.check ? 'An update is available' : 'Updated'} icon={result.publisher_key ? ShieldCheck : ShieldAlert}
          actions={
            <>
              {result.publisher_key ? <Chip kind="ok" icon={ShieldCheck}>Signed release, publisher key pinned</Chip> : <Chip kind="muted">Not signed</Chip>}
              <Button small variant="ghost" icon={FileDiff} disabled={!hasWhatChanged(result)} onClick={() => showChanges(title, result)}
                title={hasWhatChanged(result) ? 'The files this release changes, and the notes from its publisher' : "This release's publisher shows no file names or notes"}>
                What changed
              </Button>
            </>
          }
          summary={`${result.add} new, ${result.replace} changed, ${result.remove} removed, ${result.same} unchanged; ${human(toDownload(result))} ${result.check ? 'to download' : 'downloaded'}`}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="New" value={result.add} accent={result.add > 0} />
            <Stat label="Changed" value={result.replace} accent={result.replace > 0} />
            <Stat label="Removed" value={result.remove} />
            <Stat label="Unchanged" value={result.same} />
            <Stat label={result.check ? 'To download' : 'Downloaded'} value={human(toDownload(result))} accent={toDownload(result) > 0} />
          </div>
          {rebuiltLine(result, human) && <p className="hint">{rebuiltLine(result, human)}</p>}
          {result.kept.length > 0 && (
            <p className="hint">Kept because you changed them: {result.kept.slice(0, 5).join(', ')}{result.kept.length > 5 ? ` and ${result.kept.length - 5} more` : ''}</p>
          )}
          {nameLines(result).length > 0 && (
            <p className="hint selectable break-all">{nameLines(result).join('\n')}</p>
          )}
          {result.notes && (
            <div className="mt-4">
              <p className="label">From the publisher</p>
              <p className="selectable max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 text-sm text-slate-200">
                {result.notes}
              </p>
            </div>
          )}
          {result.publisher_key && <p className="selectable mt-3 break-all font-mono text-xs text-violet-300">{result.publisher_key}</p>}
        </Card>
      )}
    </div>
  )
}
