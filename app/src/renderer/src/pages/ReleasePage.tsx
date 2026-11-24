// The Release page: what to release, where players download it from, and one button that publishes the
// folder and sends only what changed there (or serves it on this computer, to try it first). Publishing
// the same version again with changed files is fine: players get the changed files (a shadow patch).
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Copy, Globe, HardDrive, KeyRound, Lock, Monitor, Package, PenLine, Plug, RefreshCw, Rocket,
  Server as ServerIcon, ShieldAlert, ShieldCheck, Square, Unlock
} from 'lucide-react'
import { useApp } from '../store/app'
import { ask, HostKey } from '../store/modal'
import { Banner, Button, Card, Chip, Field, Segmented, SettingToggle, Stat, Toggle, human } from '../components/ui'
import { PathList, listOf } from '../components/PathList'

type Mode = 'sftp' | 'folder' | 'local'
type Data = Record<string, unknown>

interface SshStatus {
  key: string
  key_exists: boolean
  has_passphrase?: boolean
  permission_problems?: string[]
  agent: 'keys' | 'empty' | 'none'
  in_agent?: boolean
  host?: string
  host_trusted?: boolean
  host_error?: string
}

interface SignStatus {
  path: string
  exists: boolean
  has_passphrase: boolean
  can_sign: boolean
}

export function modeOf(v: unknown): Mode {
  return v === 'folder' ? 'folder' : v === 'local' ? 'local' : 'sftp'
}

async function signStatus(path: string): Promise<SignStatus | null> {
  const r = await useApp.getState().run('', ['sign', 'status', `--path=${path}`], { quiet: true })
  return r.ok ? (r.data as unknown as SignStatus) : null
}

function hostArgs(): string[] {
  const st = useApp.getState()
  const s = st.settings
  const a: string[] = []
  if (s.ssh_host) a.push(`--host=${String(s.ssh_host)}`)
  if (s.ssh_user) a.push(`--user=${String(s.ssh_user)}`)
  a.push(`--port=${String(st.effective('ssh_port'))}`)
  a.push(`--key=${String(st.effective('ssh_key'))}`)
  return a
}

// Confirm a server's host key with the person: a first contact shows the keys; a changed key is a
// warning, and the old key is forgotten only if they say the server really was reinstalled.
async function confirmHost(host: string, keys: HostKey[], changed: boolean): Promise<boolean> {
  const st = useApp.getState()
  if (changed) {
    const really = await ask.confirm(
      "The server's host key has CHANGED",
      `${host} no longer offers the host key you confirmed before.\n\nThis happens when the server was reinstalled, and also when someone is between you and the server. Continue only if you know it was reinstalled, and compare its new fingerprint on the next screen.`,
      'It was reinstalled: show the new key',
      true
    )
    if (!really) return false
  }
  if (!(await ask.hostKeys(host, keys))) return false
  if (changed) {
    const f = await st.run('Forget the old host key', ['ssh', 'trust', ...hostArgs(), '--forget'])
    if (!f.ok) return false
  }
  const t = await st.run('Trust host key', ['ssh', 'trust', ...hostArgs(), `--accept=${keys.map((k) => k.fingerprint).join(',')}`])
  return t.ok
}

// run an SSH step; on an unknown host or a key others can read, ask, fix, and run it again
async function withHostTrust(label: string, args: string[]): Promise<Data | null> {
  const st = useApp.getState()
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await st.run(label, args)
    if (r.ok) return r.data
    if (r.code === 'host-unknown') {
      if (!(await confirmHost(String(r.data.host ?? ''), (r.data.keys ?? []) as HostKey[], false))) return null
      continue
    }
    if (r.code === 'key-permissions') {
      const yes = await ask.confirm('Your private key is readable by others', `${((r.data.problems ?? []) as string[]).join('\n')}\n\nOpenSSH refuses such a key. Restrict it to your account now?`, 'Restrict it')
      if (!yes) return null
      const f = await st.run('Fix permissions', ['ssh', 'fix-permissions', `--path=${String(r.data.key)}`])
      if (!f.ok) return null
      continue
    }
    return null
  }
  return null
}

// Publish the folder. The engine keeps the store: the saved one, or its own folder for this release folder.
export async function publishRelease(): Promise<Data | null> {
  const st = useApp.getState()
  const s = st.settings
  const src = String(s.publish_src || '')
  if (!src) {
    await ask.info('Choose the folder to release', 'Step 1: the folder with the files exactly as players should have them.')
    return null
  }
  const args = ['publish', src]
  if (s.store_dir) args.push(String(s.store_dir))
  args.push(`--name=${String(s.name || '')}`, `--version=${String(s.version || '')}`)
  // both lists every time: an empty one is sent as empty, so it means none (not the saved list)
  for (const [option, setting] of [['--protect', 'protect'], ['--leave-out', 'leave_out']]) {
    const items = listOf(s[setting])
    args.push(...(items.length ? items.map((p) => `${option}=${p}`) : [`${option}=`]))
  }
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
  return r.ok ? r.data : null
}

// The Release button: publish, then send only what changed (or serve it here)
export async function runRelease(dry = false): Promise<void> {
  const st = useApp.getState()
  const s = st.settings
  const mode = modeOf(s.deploy_mode)
  if (mode === 'sftp' && (!s.ssh_host || !s.remote_root)) {
    await ask.info('Where is your server?', 'Step 2: fill in the host, and the remote folder: the folder its web server gives players.')
    return
  }
  if (mode === 'folder' && !s.deploy_folder) {
    await ask.info('Which folder?', 'Step 2: choose the folder to copy the release to.')
    return
  }
  const pub = await publishRelease()
  if (!pub) return
  const store = String(pub.store)
  if (mode === 'local') {
    st.setResult('release', { publish: pub, players_url: `http://localhost:${String(st.effective('serve_port') || 8000)}/`, mode })
    if (!st.serving) void st.startServe(store)
    return
  }
  const args = ['deploy', store]
  if (mode === 'folder') args.push(`--to-folder=${String(s.deploy_folder)}`)
  else args.push(...hostArgs(), `--remote-root=${String(s.remote_root)}`)
  if (dry) args.push('--dry-run')
  const d = await withHostTrust(dry ? 'Send (dry run)' : 'Send', args)
  if (!d) return
  st.setResult('release', { publish: pub, deploy: d, players_url: String(s.public_url || ''), mode })
}

function ServerSettings(): JSX.Element {
  const busy = useApp((s) => s.busy)
  const settings = useApp((s) => s.settings)
  const keyPath = useApp((s) => String(s.effective('ssh_key')))
  const [status, setStatus] = useState<SshStatus | null>(null)
  const seq = useRef(0)

  // only the newest answer counts: a slow status check must not overwrite a newer one
  const refresh = useCallback(async () => {
    const mine = ++seq.current
    const r = await useApp.getState().run('', ['ssh', 'status', ...hostArgs()], { quiet: true })
    if (mine === seq.current) setStatus(r.ok ? (r.data as unknown as SshStatus) : null)
  }, [])
  useEffect(() => {
    const t = setTimeout(refresh, 500)
    return () => clearTimeout(t)
  }, [settings.ssh_key, settings.ssh_host, settings.ssh_port, refresh])

  const newKey = async (): Promise<void> => {
    const p = await ask.passphrase('New SSH key', `A passphrase for the new key at\n${keyPath}\n\nYou type it once per session (Unlock key). It is not saved anywhere.`, true, 10)
    if (p === null) return
    const r = await useApp.getState().run('New SSH key', ['ssh', 'keygen', `--path=${keyPath}`, '--passphrase-stdin'], { passphrase: p })
    if (r.ok) {
      await window.api.copy(String(r.data.authorized_keys_line))
      await ask.info('Key made', 'The line for the server is on the clipboard. Add it as one line to ~/.ssh/authorized_keys of the deploy user on the server: it allows SFTP only, nothing else. Then Unlock key, and Check host key.', String(r.data.authorized_keys_line))
    }
    refresh()
  }
  const copyLine = async (): Promise<void> => {
    const r = await useApp.getState().run('Server line', ['ssh', 'pubkey', `--path=${keyPath}`])
    if (r.ok) {
      await window.api.copy(String(r.data.authorized_keys_line))
      await ask.info('Copied', 'Add this as one line to ~/.ssh/authorized_keys of the deploy user on the server:', String(r.data.authorized_keys_line))
    }
  }
  const unlock = async (): Promise<void> => {
    if (status?.agent === 'none') {
      await ask.info('ssh-agent is not running', 'Windows: in an administrator PowerShell, once:', 'Get-Service ssh-agent | Set-Service -StartupType Automatic\nStart-Service ssh-agent')
      return
    }
    const p = await ask.passphrase('Unlock key', `The passphrase of\n${keyPath}\n\nssh-agent keeps the key unlocked until you log out.`)
    if (p === null) return
    await useApp.getState().run('Unlock key', ['ssh', 'unlock', `--path=${keyPath}`, '--passphrase-stdin'], { passphrase: p })
    refresh()
  }
  const fixPerms = async (): Promise<void> => {
    await useApp.getState().run('Fix permissions', ['ssh', 'fix-permissions', `--path=${keyPath}`])
    refresh()
  }
  const checkHost = async (): Promise<void> => {
    const r = await useApp.getState().run('Check host key', ['ssh', 'scan', ...hostArgs()])
    if (!r.ok) return
    const keys = r.data.keys as HostKey[]
    if (r.data.state === 'trusted') {
      await ask.info('Host key confirmed', `${String(r.data.host)} offers the host key you confirmed before.`)
    } else {
      await confirmHost(String(r.data.host), keys, r.data.state === 'changed')
    }
    refresh()
  }
  const test = async (): Promise<void> => {
    const s = useApp.getState().settings
    const args = ['ssh', 'test', ...hostArgs()]
    if (s.remote_root) args.push(`--remote-root=${String(s.remote_root)}`)
    await withHostTrust('Test connection', args)
    refresh()
  }

  const perms = status?.permission_problems ?? []
  return (
    <div className="space-y-5">
      <div className="grid gap-5 md:grid-cols-6">
        <Field className="md:col-span-3" label="Host" setting="ssh_host" placeholder="game.example.com" mono />
        <Field className="md:col-span-2" label="User" setting="ssh_user" placeholder="deploy" mono />
        <Field className="md:col-span-1" label="Port" setting="ssh_port" type="number" />
        <Field className="md:col-span-3" label="Remote folder" setting="remote_root" placeholder="/var/www/game" mono
          hint={'The folder on the server its web server serves.\nForward slashes, even on a Windows server.'} />
        <Field className="md:col-span-3" label="Players download from" setting="public_url" placeholder="https://game.example.com/" mono
          hint={'The address the web server gives that folder.\nPlayers put it on their Update page.'} />
      </div>
      <div className="rounded-lg border border-ink-700 bg-ink-900/60 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-sm font-semibold text-slate-100"><KeyRound size={15} className="text-delta" />Connection</span>
          <Button small variant="ghost" icon={RefreshCw} onClick={refresh}>Refresh</Button>
        </div>
        <Field label="SSH key file" setting="ssh_key" browse="file" mono hint="Read only by OpenSSH. DeltaPatcher never opens your private key." />
        <div className="mt-3 flex flex-wrap gap-2">
          {status?.host_error && <Chip kind="bad" icon={ShieldAlert}>{status.host_error}</Chip>}
          {status?.host && (status.host_trusted ? <Chip kind="ok" icon={ShieldCheck}>Host key confirmed</Chip> : <Chip kind="warn">Host key not confirmed yet</Chip>)}
          {!status?.key_exists && <Chip kind="muted">No key at this path yet</Chip>}
          {status?.key_exists && (status.has_passphrase ? <Chip kind="ok" icon={Lock}>Passphrase protected</Chip> : <Chip kind="warn">No passphrase</Chip>)}
          {status?.key_exists && perms.length > 0 && <Chip kind="bad">Readable by others</Chip>}
          {status?.key_exists && (status.agent === 'none' ? <Chip kind="warn">ssh-agent not running</Chip> : status.in_agent ? <Chip kind="ok" icon={Unlock}>Unlocked</Chip> : <Chip kind="muted">Locked</Chip>)}
        </div>
        {perms.length > 0 && <div className="mt-3"><Banner kind="bad">{perms.join('; ')}. OpenSSH refuses a key others can read: use Fix permissions.</Banner></div>}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button small icon={KeyRound} disabled={Boolean(busy) || Boolean(status?.key_exists)} onClick={newKey}>New key</Button>
          <Button small icon={Copy} disabled={Boolean(busy) || !status?.key_exists} onClick={copyLine}>Copy server line</Button>
          <Button small icon={Unlock} disabled={Boolean(busy) || !status?.key_exists || !status?.has_passphrase} onClick={unlock}>Unlock key</Button>
          <Button small icon={Lock} disabled={Boolean(busy) || !perms.length} onClick={fixPerms}>Fix permissions</Button>
          <Button small icon={ShieldCheck} disabled={Boolean(busy) || !settings.ssh_host} onClick={checkHost}>Check host key</Button>
          <Button small icon={Plug} disabled={Boolean(busy) || !settings.ssh_host} onClick={test}>Test connection</Button>
        </div>
      </div>
      <SettingToggle setting="htaccess" label=".htaccess for Apache" hint="Serve only the release: no listings, the right cache headers." />
    </div>
  )
}

function SigningCard(): JSX.Element {
  const busy = useApp((s) => s.busy)
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
    <Card title="Sign releases (optional)" icon={PenLine}
      actions={keyStatus?.exists ? <Chip kind="ok" icon={KeyRound}>Key ready{keyStatus.has_passphrase ? ', passphrase protected' : ''}</Chip> : <Chip kind="muted">No key yet</Chip>}>
      <div className="grid gap-5">
        <SettingToggle setting="sign" label="Sign each release" hint="Players' updaters then refuse anything not signed by your key." />
        <Field label="Signing key" setting="signing_key" browse="file" mono hint="The file's path is kept, never the key. Readable by your account only." />
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <Button icon={KeyRound} disabled={Boolean(busy) || Boolean(keyStatus?.exists)} onClick={newKey}>New signing key</Button>
        <Button icon={Copy} disabled={Boolean(busy) || !keyStatus?.exists} onClick={copyKey}>Copy public key</Button>
      </div>
      {publicKey && <p className="selectable mt-4 break-all rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-xs text-delta-light">{publicKey}</p>}
    </Card>
  )
}

function ReleaseResult(props: { r: Data }): JSX.Element {
  const serving = useApp((s) => s.serving)
  const pub = (props.r.publish ?? {}) as Data
  const dep = props.r.deploy as Data | undefined
  const url = String(props.r.players_url || '')
  const leftOut = (pub.left_out ?? []) as string[]
  return (
    <div className="mt-5 space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Files" value={Number(pub.files)} />
        <Stat label="Size" value={human(Number(pub.bytes))} />
        <Stat label="New in this release" value={`${Number(pub.new_blobs)} (${human(Number(pub.new_bytes))})`} accent={Number(pub.new_blobs) > 0} />
        {dep ? (
          <Stat label={dep.dry_run ? 'Would send' : 'Sent'} value={`${Number(dep.sent)} (${human(Number(dep.sent_bytes))})`} accent />
        ) : (
          <Stat label="This computer" value={serving?.url ? 'Serving' : 'Stopped'} accent={Boolean(serving?.url)} />
        )}
      </div>
      {leftOut.length > 0 && (
        <p className="hint">Left out: {leftOut.slice(0, 12).join(', ')}{leftOut.length > 12 ? ` and ${leftOut.length - 12} more` : ''}</p>
      )}
      {url ? (
        <Banner kind="info">
          <div className="flex flex-wrap items-center gap-3">
            <span>Players update from <span className="selectable font-mono">{url}</span></span>
            <Button small icon={Copy} onClick={() => window.api.copy(url)}>Copy</Button>
          </div>
        </Banner>
      ) : (
        <p className="hint">Fill in "Players download from" in step 2 to see here the address players use.</p>
      )}
    </div>
  )
}

export default function ReleasePage(): JSX.Element {
  const busy = useApp((s) => s.busy)
  const settings = useApp((s) => s.settings)
  const set = useApp((s) => s.set)
  const serving = useApp((s) => s.serving)
  const stopServe = useApp((s) => s.stopServe)
  const port = useApp((s) => String(s.effective('serve_port') || 8000))
  const result = useApp((s) => s.results.release as Data | undefined)
  const [dry, setDry] = useState(false)
  const mode = modeOf(settings.deploy_mode)

  const where =
    mode === 'sftp'
      ? settings.ssh_host
        ? `${settings.ssh_user ? String(settings.ssh_user) + '@' : ''}${String(settings.ssh_host)}`
        : 'your server'
      : mode === 'folder'
        ? String(settings.deploy_folder || 'the folder')
        : `http://localhost:${port}/`

  return (
    <div className="space-y-5">
      <Card title="1. What to release" icon={Package}>
        <div className="grid gap-5 md:grid-cols-2">
          <Field className="md:col-span-2" label="Folder to release" setting="publish_src" browse="folder" mono
            hint={'The files exactly as players should have them.\nAny type: zip, pk3, textures, programs.'} />
          <Field label="Name" setting="name" placeholder="My Game" />
          <Field label="Version" setting="version" placeholder="1.0"
            hint="Shown to players. A new version or the same one: either way only what changed is sent." />
          <PathList setting="leave_out" label="Leave out of the release" empty="Nothing picked: the whole folder is released."
            hint={'Players never get these. A folder leaves out everything in it.\nAlways left out: .git, .svn, .hg, __pycache__, Thumbs.db, desktop.ini, .DS_Store, *.tmp.'} />
          <PathList setting="protect" label="Players keep their own copy" empty="Nothing picked: every file follows the release."
            hint={'Sent once, then never overwritten: players keep their changes (settings, saves).\nA folder covers everything in it.'} />
        </div>
      </Card>

      <Card title="2. Where players download it from" icon={Globe}>
        <Segmented value={mode} onChange={(v) => set('deploy_mode', v)}
          options={[
            { value: 'sftp', label: 'Your server (SSH)', icon: ServerIcon },
            { value: 'folder', label: 'A folder', icon: HardDrive },
            { value: 'local', label: 'This computer, to test', icon: Monitor }
          ]} />
        <div className="mt-5">
          {mode === 'sftp' && <ServerSettings />}
          {mode === 'folder' && (
            <div className="grid gap-5 md:grid-cols-2">
              <Field className="md:col-span-2" label="Copy it to" setting="deploy_folder" browse="folder" mono
                hint={'A web root on this computer, or a network share.\nA web server then gives it to players.'} />
              <Field className="md:col-span-2" label="Players download from" setting="public_url" placeholder="https://game.example.com/" mono
                hint={'The address the web server gives that folder.\nPlayers put it on their Update page.'} />
              <SettingToggle setting="htaccess" label=".htaccess for Apache" hint="Serve only the release: no listings, the right cache headers." />
            </div>
          )}
          {mode === 'local' && (
            <div className="flex flex-wrap items-end gap-4">
              <Field className="w-32" label="Port" setting="serve_port" type="number" />
              <p className="hint max-w-xl">
                Release serves it at <span className="font-mono">http://localhost:{port}/</span>, for this computer only:
                <br />
                put that address on the Update page to try the release before players get it.
              </p>
            </div>
          )}
        </div>
      </Card>

      <SigningCard />

      <Card title="3. Release" icon={Rocket}>
        <p className="text-sm text-ink-300">
          {mode === 'local'
            ? `Publishes the folder and serves it at ${where}, on this computer only.`
            : `Publishes the folder and sends ${where} only what changed.`}
          <br />
          {mode === 'local'
            ? 'Stop serving, or close DeltaPatcher, and it is gone.'
            : 'The new files go first and the list of files last, so players never see half a release.'}
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button variant="primary" icon={Rocket} disabled={Boolean(busy)} onClick={() => runRelease(dry)}>Release</Button>
          {mode !== 'local' && <div className="ml-2"><Toggle checked={dry} onChange={setDry} label="Dry run" hint="Say what would be sent; send nothing." /></div>}
          {serving && <Button icon={Square} onClick={stopServe}>Stop serving</Button>}
        </div>
        {result && <ReleaseResult r={result} />}
      </Card>
    </div>
  )
}
