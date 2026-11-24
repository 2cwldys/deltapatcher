import { useEffect, useState } from 'react'
import { UploadCloud, Radio, Square, PenLine, KeyRound, Copy, Package, Globe } from 'lucide-react'
import { useApp } from '../store/app'
import { ask } from '../store/modal'
import { Button, Card, Chip, Field, SettingToggle, Stat, human } from '../components/ui'

interface SignStatus {
  path: string
  exists: boolean
  has_passphrase: boolean
  can_sign: boolean
}

async function signStatus(path: string): Promise<SignStatus | null> {
  const r = await useApp.getState().run('', ['sign', 'status', `--path=${path}`], { quiet: true })
  return r.ok ? (r.data as SignStatus) : null
}

export async function runPublish(): Promise<Record<string, number | boolean | string> | null> {
  const st = useApp.getState()
  const s = st.settings
  const src = String(s.publish_src || '')
  const store = String(s.store_dir || '')
  if (!src || !store) {
    await ask.info('Choose the folders', 'Choose the folder to publish and the store folder first.')
    return null
  }
  const args = ['publish', src, store, `--name=${String(s.name || '')}`, `--version=${String(s.version || '')}`]
  for (const p of String(s.protect || '').split(',').map((x) => x.trim()).filter(Boolean)) args.push(`--protect=${p}`)
  if (!s.htaccess) args.push('--no-htaccess')
  let passphrase: string | undefined
  if (s.sign) {
    const key = String(st.effective('signing_key'))
    const status = await signStatus(key)
    if (!status?.exists) {
      await ask.info('No signing key', `There is no signing key at\n${key}\n\nMake one with New signing key, or turn signing off.`)
      return null
    }
    args.push(`--sign=${key}`)
    if (status.has_passphrase) {
      const p = await ask.passphrase('Sign the release', `The passphrase of the signing key\n${key}`)
      if (p === null) return null
      passphrase = p
      args.push('--passphrase-stdin')
    }
  }
  const r = await st.run('Publish', args, { passphrase })
  if (r.ok) st.setResult('publish', r.data)
  return r.ok ? r.data : null
}

export default function PublishPage(): JSX.Element {
  const busy = useApp((s) => s.busy)
  const serving = useApp((s) => s.serving)
  const startServe = useApp((s) => s.startServe)
  const stopServe = useApp((s) => s.stopServe)
  const result = useApp((s) => s.results.publish as Record<string, number | boolean | string> | undefined)
  const keyPath = useApp((s) => String(s.effective('signing_key')))
  const [publicKey, setPublicKey] = useState('')
  const [keyStatus, setKeyStatus] = useState<SignStatus | null>(null)

  useEffect(() => {
    let live = true
    const t = setTimeout(async () => {
      const s = keyPath ? await signStatus(keyPath) : null
      if (live) setKeyStatus(s)
    }, 400)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [keyPath])

  const newKey = async (): Promise<void> => {
    const path = keyPath
    const p = await ask.passphrase('New signing key', `A passphrase for the new signing key at\n${path}\n\nIt protects the key if the file is ever copied. Back the key up: it cannot be made again.`, true, 10)
    if (p === null) return
    const r = await useApp.getState().run('New signing key', ['sign', 'keygen', `--path=${path}`, '--passphrase-stdin'], { passphrase: p })
    if (r.ok) {
      setPublicKey(String(r.data.public_key))
      useApp.getState().set('sign', true)
      setKeyStatus(await signStatus(path))
      await window.api.copy(String(r.data.public_key))
      await ask.info('Signing key made', 'Signing is on. The public key is on the clipboard: give it to players, or put it in your launcher, so their updater checks that your releases are yours.', String(r.data.public_key))
    }
  }

  const copyKey = async (): Promise<void> => {
    const args = ['sign', 'pubkey', `--path=${keyPath}`]
    let passphrase: string | undefined
    if (keyStatus?.has_passphrase) {
      const p = await ask.passphrase('Public key', `The passphrase of the signing key\n${keyPath}`)
      if (p === null) return
      passphrase = p
      args.push('--passphrase-stdin')
    }
    const r = await useApp.getState().run('Public key', args, { passphrase })
    if (r.ok) {
      setPublicKey(String(r.data.public_key))
      await window.api.copy(String(r.data.public_key))
    }
  }

  return (
    <div className="space-y-5">
      <Card title="Release" icon={Package}>
        <div className="grid gap-5 md:grid-cols-2">
          <Field className="md:col-span-2" label="Folder to release" setting="publish_src" browse="folder" mono
            hint="The files exactly as players should have them. Any type: zip, pk3, textures, programs." />
          <Field className="md:col-span-2" label="Store" setting="store_dir" browse="folder" mono
            hint="Where manifest.json, blobs/ and sigs/ are written: what gets served and deployed." />
          <Field label="Name" setting="name" placeholder="My Game" />
          <Field label="Version" setting="version" placeholder="1.0" />
          <Field className="md:col-span-2" label="Protect" setting="protect" placeholder="config/*.cfg, settings.ini" mono
            hint="Files players keep once they have them (their settings). Comma-separated globs." />
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <SettingToggle setting="htaccess" label=".htaccess for Apache" hint="Serve only the release: no listings, the right cache headers." />
        </div>
        <div className="mt-6 flex flex-wrap gap-2">
          <Button variant="primary" icon={UploadCloud} disabled={Boolean(busy)} onClick={() => runPublish()}>Publish</Button>
        </div>
      </Card>

      <Card title="Signing" icon={PenLine}
        actions={keyStatus?.exists ? <Chip kind="ok" icon={KeyRound}>Key ready{keyStatus.has_passphrase ? ', passphrase protected' : ''}</Chip> : <Chip kind="muted">No key yet</Chip>}>
        <div className="grid gap-5">
          <SettingToggle setting="sign" label="Sign each release" hint="Optional. Players' updaters then refuse anything not signed by your key." />
          <Field label="Signing key" setting="signing_key" browse="file" mono hint="The file's path is kept, never the key. Readable by your account only." />
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button icon={KeyRound} disabled={Boolean(busy) || Boolean(keyStatus?.exists)} onClick={newKey}>New signing key</Button>
          <Button icon={Copy} disabled={Boolean(busy) || !keyStatus?.exists} onClick={copyKey}>Copy public key</Button>
        </div>
        {publicKey && <p className="selectable mt-4 break-all rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-xs text-violet-300">{publicKey}</p>}
      </Card>

      <Card title="Try it here" icon={Radio} actions={serving?.url ? <Chip kind="ok" icon={Globe}>Serving</Chip> : undefined}>
        <div className="flex flex-wrap items-end gap-4">
          <Field className="w-32" label="Port" setting="serve_port" type="number" />
          <Button variant={serving ? 'secondary' : 'primary'} icon={serving ? Square : Radio} onClick={serving ? stopServe : startServe}>
            {serving ? 'Stop serving' : 'Serve locally'}
          </Button>
        </div>
        {serving?.url && (
          <p className="hint">
            The store is at <span className="selectable font-mono text-violet-300">{serving.url}</span> on this computer, until you stop it or close DeltaPatcher: use it as the Source on the Update page.
          </p>
        )}
      </Card>

      {result && (
        <Card title={result.signed ? 'Published and signed' : 'Published'} icon={UploadCloud}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Files" value={Number(result.files)} />
            <Stat label="Size" value={human(Number(result.bytes))} />
            <Stat label="New blobs" value={Number(result.new_blobs)} accent={Number(result.new_blobs) > 0} />
            <Stat label="New data" value={human(Number(result.new_bytes))} accent={Number(result.new_bytes) > 0} />
          </div>
        </Card>
      )}
    </div>
  )
}
