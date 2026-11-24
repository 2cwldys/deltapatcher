import { Download, Search, Undo2, ShieldCheck, ShieldAlert, FolderSync } from 'lucide-react'
import { useApp } from '../store/app'
import { ask } from '../store/modal'
import { Banner, Button, Card, Chip, Field, Stat, human } from '../components/ui'

interface Result {
  empty: boolean
  add: number
  replace: number
  remove: number
  same: number
  download_bytes: number
  download_blobs: number
  publisher_key: string
  kept: string[]
  check: boolean
}

function insecure(url: string): boolean {
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
  const args = ['update', folder, `--source=${source}`]
  const key = String(st.settings.trust_key || '').trim()
  if (key) args.push(`--trust-key=${key}`)
  if (check) args.push('--check')
  const r = await st.run(check ? 'Check' : 'Update', args, { cancellable: !check })
  if (!r.ok) return null
  const result = { ...(r.data as Result), check }
  st.setResult('update', result)
  return result
}

export default function UpdatePage(): JSX.Element {
  const busy = useApp((s) => s.busy)
  const source = useApp((s) => String(s.settings.source_url || ''))
  const result = useApp((s) => s.results.update as Result | undefined)

  const rollback = async (): Promise<void> => {
    const folder = String(useApp.getState().settings.install_dir || '')
    if (!folder) return
    if (!(await ask.confirm('Roll back', `Put back the files the last update replaced or removed in\n${folder}?`, 'Roll back'))) return
    const r = await useApp.getState().run('Roll back', ['rollback', folder])
    if (r.ok) useApp.getState().setResult('update', undefined)
  }

  return (
    <div className="space-y-5">
      <Card title="Update a folder" icon={FolderSync}>
        <div className="grid gap-5">
          <Field label="Source" setting="source_url" mono
            hint={"Where the release is: an http(s) address or a folder.\nThe game's maker gives you this."} />
          {insecure(source) && <Banner kind="warn">This address is plain <b>http</b> to another machine. Use https, or a signed release with the publisher key below.</Banner>}
          <Field label="Folder" setting="install_dir" browse="folder" placeholder="The game folder" mono
            hint={'The folder to bring up to date.\nFiles you added yourself are never removed.'} />
          <Field label="Publisher key (optional)" setting="trust_key" placeholder="ed25519:..." mono
            hint={"Paste the key the publisher gave you: only releases they signed are installed.\nEmpty: the first signed release's key is trusted, and only that key after it."} />
        </div>
        <div className="mt-6 flex flex-wrap gap-2">
          <Button variant="primary" icon={Download} disabled={Boolean(busy)} onClick={() => runUpdate(false)}>Update</Button>
          <Button icon={Search} disabled={Boolean(busy)} onClick={() => runUpdate(true)}>Check</Button>
          <Button variant="ghost" icon={Undo2} disabled={Boolean(busy)} onClick={rollback}>Roll back</Button>
        </div>
      </Card>

      {result && (
        <Card title={result.empty ? 'Up to date' : result.check ? 'An update is available' : 'Updated'} icon={result.publisher_key ? ShieldCheck : ShieldAlert}
          actions={result.publisher_key ? <Chip kind="ok" icon={ShieldCheck}>Signed release, publisher key pinned</Chip> : <Chip kind="muted">Not signed</Chip>}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="New" value={result.add} accent={result.add > 0} />
            <Stat label="Changed" value={result.replace} accent={result.replace > 0} />
            <Stat label="Removed" value={result.remove} />
            <Stat label="Unchanged" value={result.same} />
            <Stat label={result.check ? 'To download' : 'Downloaded'} value={human(result.download_bytes)} accent={result.download_bytes > 0} />
          </div>
          {result.kept.length > 0 && (
            <p className="hint">Kept because you changed them: {result.kept.slice(0, 5).join(', ')}{result.kept.length > 5 ? ` and ${result.kept.length - 5} more` : ''}</p>
          )}
          {result.publisher_key && <p className="selectable mt-3 break-all font-mono text-xs text-violet-300">{result.publisher_key}</p>}
        </Card>
      )}
    </div>
  )
}
