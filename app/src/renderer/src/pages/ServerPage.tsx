import { useCallback, useEffect, useRef, useState } from 'react'
import { Rocket, ShieldCheck, ShieldAlert, Plug, KeyRound, Copy, Unlock, Lock, HardDrive, Server as ServerIcon, RefreshCw } from 'lucide-react'
import { useApp } from '../store/app'
import { ask, HostKey } from '../store/modal'
import { Banner, Button, Card, Chip, Field, Segmented, Stat, Toggle, human } from '../components/ui'

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
  key_error?: string
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
      'The server\'s host key has CHANGED',
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
async function withHostTrust(label: string, args: string[]): Promise<Record<string, unknown> | null> {
  const st = useApp.getState()
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await st.run(label, args)
    if (r.ok) return r.data
    if (r.code === 'host-unknown') {
      if (!(await confirmHost(String(r.data.host ?? ''), (r.data.keys ?? []) as HostKey[], false))) return null
      continue
    }
    if (r.code === 'key-permissions') {
      const yes = await ask.confirm('Your private key is readable by others', `${(r.data.problems ?? []).join('\n')}\n\nOpenSSH refuses such a key. Restrict it to your account now?`, 'Restrict it')
      if (!yes) return null
      const f = await st.run('Fix permissions', ['ssh', 'fix-permissions', `--path=${String(r.data.key)}`])
      if (!f.ok) return null
      continue
    }
    return null
  }
  return null
}

export default function ServerPage(): JSX.Element {
  const busy = useApp((s) => s.busy)
  const settings = useApp((s) => s.settings)
  const set = useApp((s) => s.set)
  const keyPath = useApp((s) => String(s.effective('ssh_key')))
  const [status, setStatus] = useState<SshStatus | null>(null)
  const [dry, setDry] = useState(false)
  const [result, setResult] = useState<Record<string, number | boolean | string> | null>(null)
  const seq = useRef(0)
  const mode: 'sftp' | 'folder' = settings.deploy_mode === 'folder' ? 'folder' : 'sftp'

  // only the newest answer counts: a slow status check must not overwrite a newer one
  const refresh = useCallback(async () => {
    const mine = ++seq.current
    const r = await useApp.getState().run('', ['ssh', 'status', ...hostArgs()], { quiet: true })
    if (mine === seq.current) setStatus(r.ok ? (r.data as SshStatus) : null)
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
      await ask.info('Host key confirmed', `${r.data.host} offers the host key you confirmed before.`)
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
  const deploy = async (): Promise<void> => {
    const s = useApp.getState().settings
    const store = String(s.store_dir || '')
    if (!store) {
      await ask.info('No store', 'Set the store folder on the Publish page, and publish, first.')
      return
    }
    const args = ['deploy', store]
    if (mode === 'folder') {
      if (!s.deploy_folder) return void (await ask.info('Choose a folder', 'Choose the folder to deploy to.'))
      args.push(`--to-folder=${String(s.deploy_folder)}`)
    } else {
      if (!s.remote_root) return void (await ask.info('Remote folder', 'Enter the folder the server serves, for example /var/www/game.'))
      args.push(...hostArgs(), `--remote-root=${String(s.remote_root)}`)
    }
    if (dry) args.push('--dry-run')
    const d = await withHostTrust(dry ? 'Deploy (dry run)' : 'Deploy', args)
    if (d) setResult(d as Record<string, number | boolean | string>)
    refresh()
  }

  const perms = status?.permission_problems ?? []
  return (
    <div className="space-y-5">
      <Segmented value={mode} onChange={(v) => set('deploy_mode', v)}
        options={[{ value: 'sftp', label: 'A server over SSH', icon: ServerIcon }, { value: 'folder', label: 'A folder', icon: HardDrive }]} />

      {mode === 'sftp' ? (
        <>
          <Card title="Server" icon={ServerIcon} actions={<Button small variant="ghost" icon={RefreshCw} onClick={refresh}>Refresh</Button>}>
            <div className="grid gap-5 md:grid-cols-6">
              <Field className="md:col-span-3" label="Host" setting="ssh_host" placeholder="game.example.com" mono />
              <Field className="md:col-span-2" label="User" setting="ssh_user" placeholder="deploy" mono />
              <Field className="md:col-span-1" label="Port" setting="ssh_port" type="number" />
              <Field className="md:col-span-6" label="Remote folder" setting="remote_root" placeholder="/var/www/game" mono
                hint="The folder on the server its web server serves, with forward slashes." />
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              {status?.host_error && <Chip kind="bad" icon={ShieldAlert}>{status.host_error}</Chip>}
              {status?.host && (status.host_trusted ? <Chip kind="ok" icon={ShieldCheck}>Host key confirmed</Chip> : <Chip kind="warn">Host key not confirmed yet</Chip>)}
            </div>
          </Card>

          <Card title="SSH key" icon={KeyRound}>
            <Field label="Key file" setting="ssh_key" browse="file" mono hint="Read only by OpenSSH. DeltaPatcher never opens your private key." />
            <div className="mt-4 flex flex-wrap gap-2">
              {!status?.key_exists && <Chip kind="muted">No key at this path yet</Chip>}
              {status?.key_exists && (status.has_passphrase ? <Chip kind="ok" icon={Lock}>Passphrase protected</Chip> : <Chip kind="warn">No passphrase</Chip>)}
              {status?.key_exists && (perms.length ? <Chip kind="bad">Readable by others</Chip> : <Chip kind="ok">Owner-only permissions</Chip>)}
              {status?.key_exists && (status.agent === 'none' ? <Chip kind="warn">ssh-agent not running</Chip> : status.in_agent ? <Chip kind="ok" icon={Unlock}>Unlocked in ssh-agent</Chip> : <Chip kind="muted">Not in ssh-agent</Chip>)}
            </div>
            {perms.length > 0 && <div className="mt-4"><Banner kind="bad">{perms.join('; ')}. OpenSSH refuses a key others can read: use Fix permissions.</Banner></div>}
            <div className="mt-5 flex flex-wrap gap-2">
              <Button icon={KeyRound} disabled={Boolean(busy) || Boolean(status?.key_exists)} onClick={newKey}>New key</Button>
              <Button icon={Copy} disabled={Boolean(busy) || !status?.key_exists} onClick={copyLine}>Copy server line</Button>
              <Button icon={Unlock} disabled={Boolean(busy) || !status?.key_exists || !status?.has_passphrase} onClick={unlock}>Unlock key</Button>
              <Button icon={Lock} disabled={Boolean(busy) || !perms.length} onClick={fixPerms}>Fix permissions</Button>
            </div>
          </Card>
        </>
      ) : (
        <Card title="Folder" icon={HardDrive}>
          <Field label="Deploy to" setting="deploy_folder" browse="folder" mono
            hint="A web root on this computer or a network share: the release is copied here, and a web server serves it." />
        </Card>
      )}

      <Card title="Deploy" icon={Rocket}>
        <p className="text-sm text-ink-300">
          Sends the store on the Publish page. Only blobs the target lacks travel; the signature and the manifest go last, so players never see half a release.
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button variant="primary" icon={Rocket} disabled={Boolean(busy)} onClick={deploy}>Deploy</Button>
          {mode === 'sftp' && <Button icon={ShieldCheck} disabled={Boolean(busy) || !settings.ssh_host} onClick={checkHost}>Check host key</Button>}
          {mode === 'sftp' && <Button icon={Plug} disabled={Boolean(busy) || !settings.ssh_host} onClick={test}>Test connection</Button>}
          <div className="ml-2"><Toggle checked={dry} onChange={setDry} label="Dry run" /></div>
        </div>
        {result && (
          <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Blobs named" value={Number(result.named)} />
            <Stat label="Already there" value={Number(result.had)} />
            <Stat label={result.dry_run ? 'Would send' : 'Sent'} value={`${result.sent} (${human(Number(result.sent_bytes))})`} accent />
            <Stat label={result.dry_run ? 'Would remove' : 'Removed'} value={Number(result.removed)} />
          </div>
        )}
      </Card>
    </div>
  )
}
