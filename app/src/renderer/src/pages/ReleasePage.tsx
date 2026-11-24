// The Release page: what to release, where players download it from, the one-time setup of the server (folded
// once it is all done), and one button that publishes the folder and sends only what changed there (or serves it
// on this computer, to try it first); then the statistics of the release players have. Publishing the same
// version again with changed files is fine: players get the changed files (a shadow patch). A project with no
// folder to release yet opens on a short start; the rest is one click away. The backups of the live release are
// on the Settings page.
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChartColumn, ChevronsDown, Copy, Globe, HardDrive, KeyRound, Lock, Monitor, Package, PenLine, Plug, RefreshCw,
  Rocket, Server as ServerIcon, ServerCog, ShieldAlert, ShieldCheck, Square, Unlock, Upload
} from 'lucide-react'
import type { EngineOutcome } from '@shared/types'
import { useApp } from '../store/app'
import { sentences } from '../store/projectActions'
import { BarRow, players, statsCounter, StatsState, statsSummary, when } from '../store/stats'
import { serverSetup, signingSetup, SignStatus, SshStatus } from '../store/setup'
import { REPO_URL } from '@shared/project'
import { ask, HostKey } from '../store/modal'
import { Banner, Button, Card, Chip, Field, Segmented, SettingToggle, Stat, Toggle, human } from '../components/ui'
import { PathList, listOf } from '../components/PathList'

type Mode = 'sftp' | 'folder' | 'local'
type Data = Record<string, unknown>

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
export async function confirmHost(host: string, keys: HostKey[], changed: boolean): Promise<boolean> {
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

// run an SSH step; on an unknown host or a key others can read, ask, fix, and run it again. `failed` hears of
// a run that ended in a failure (not one the person said no to)
export async function withHostTrust(label: string, args: string[], passphrase?: string, failed?: (r: EngineOutcome) => void): Promise<Data | null> {
  const st = useApp.getState()
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await st.run(label, args, { passphrase })
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
    failed?.(r)
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
    await ask.info('Choose the folder to release', 'The folder with the files exactly as players should have them.')
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
    await ask.info('Where is your server?', 'Fill in the host, and the remote folder: the folder its web server gives players.')
    return
  }
  if (mode === 'folder' && !s.deploy_folder) {
    await ask.info('Which folder?', 'Choose the folder to copy the release to.')
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

// the last state of each project's key, host and signing key, kept while another page or project is shown, so
// the setup part opens folded or not at once when the page is shown again
const keptSsh = new Map<string, SshStatus>()
const keptSign = new Map<string, SignStatus>()

// The SSH key and the host: what the engine says of them, read again when the key, host or port changes; only the
// newest answer counts (a slow status check must not overwrite a newer one)
function useSshStatus(on: boolean): [SshStatus | null, () => Promise<void>] {
  const project = useApp((s) => s.project)
  const settings = useApp((s) => s.settings)
  const [status, setStatus] = useState<SshStatus | null>(keptSsh.get(project) ?? null)
  const seq = useRef(0)
  const refresh = useCallback(async () => {
    const mine = ++seq.current
    const file = useApp.getState().project
    const r = await useApp.getState().run('', ['ssh', 'status', ...hostArgs()], { quiet: true })
    if (mine !== seq.current) return
    const s = r.ok ? (r.data as unknown as SshStatus) : null
    if (s) keptSsh.set(file, s)
    else keptSsh.delete(file)
    setStatus(s)
  }, [])
  useEffect(() => {
    setStatus(keptSsh.get(project) ?? null)
  }, [project])
  useEffect(() => {
    if (!on) return
    const t = setTimeout(refresh, 500)
    return () => clearTimeout(t)
  }, [on, settings.ssh_key, settings.ssh_host, settings.ssh_port, settings.ssh_user, project, refresh])
  return [status, refresh]
}

// the signing key: what the engine says of it, read again when its path changes
function useSignStatus(): [SignStatus | null, (s: SignStatus | null) => void] {
  const project = useApp((s) => s.project)
  const keyPath = useApp((s) => String(s.effective('signing_key')))
  const [status, setStatus] = useState<SignStatus | null>(keptSign.get(project) ?? null)
  const keep = useCallback((s: SignStatus | null) => {
    const file = useApp.getState().project
    if (s) keptSign.set(file, s)
    else keptSign.delete(file)
    setStatus(s)
  }, [])
  useEffect(() => {
    let live = true
    const t = setTimeout(async () => {
      const s = keyPath ? await signStatus(keyPath) : null
      if (live) keep(s)
    }, 400)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [keyPath, project, keep])
  return [status, keep]
}

// The SSH key's steps: make it, put it on the server (or copy its line), unlock it, fix who can read it
function SshKeySteps(props: { status: SshStatus | null; refresh: () => Promise<void> }): JSX.Element {
  const { status, refresh } = props
  const busy = useApp((s) => s.busy)
  const settings = useApp((s) => s.settings)
  const keyPath = useApp((s) => String(s.effective('ssh_key')))

  const newKey = async (): Promise<void> => {
    const p = await ask.passphrase('New SSH key', `A passphrase for the new key at\n${keyPath}\n\nYou type it once per session (Unlock key). It is not saved anywhere.`, true, 10)
    if (p === null) return
    const r = await useApp.getState().run('New SSH key', ['ssh', 'keygen', `--path=${keyPath}`, '--passphrase-stdin'], { passphrase: p })
    if (r.ok) {
      await window.api.copy(String(r.data.authorized_keys_line))
      await ask.info('Key made', "Next: Put key on server (it logs in once with the server's password). Or add this line yourself to ~/.ssh/authorized_keys of the user on the server: it allows SFTP only, nothing else. Then Unlock key.", String(r.data.authorized_keys_line))
    }
    void refresh()
  }
  // log in once with the server's password (used for that login only, never kept) and add the key
  const installKey = async (): Promise<void> => {
    const s = useApp.getState().settings
    if (!s.ssh_host || !s.ssh_user) {
      await ask.info('The server and the user first', 'Fill in Host and User above.')
      return
    }
    const p = await ask.passphrase('Put your key on the server', `The password of ${String(s.ssh_user)} on ${String(s.ssh_host)}.\n\nIt is used once, to add your key, and never kept: after that DeltaPatcher logs in with the key.`, false, 1)
    if (p === null) return
    const d = await withHostTrust('Put key on server', ['ssh', 'install-key', ...hostArgs(), '--passphrase-stdin'], p)
    if (d) await ask.info('Your key is on the server', `${String(d.host)} knows your key now (${String(d.result)}). Unlock key, then Test connection.`)
    void refresh()
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
    void refresh()
  }
  const fixPerms = async (): Promise<void> => {
    await useApp.getState().run('Fix permissions', ['ssh', 'fix-permissions', `--path=${keyPath}`])
    void refresh()
  }

  const perms = status?.permission_problems ?? []
  return (
    <div>
      <p className="mb-3 text-sm font-semibold text-slate-100">The SSH key</p>
      <Field label="SSH key file" setting="ssh_key" browse="file" mono hint={'Read only by OpenSSH.\nDeltaPatcher never opens your private key.'} />
      <div className="mt-3 flex flex-wrap gap-2">
        {!status?.key_exists && <Chip kind="muted">No key at this path yet</Chip>}
        {status?.key_exists && (status.has_passphrase ? <Chip kind="ok" icon={Lock}>Passphrase protected</Chip> : <Chip kind="warn">No passphrase</Chip>)}
        {status?.key_exists && perms.length > 0 && <Chip kind="bad">Readable by others</Chip>}
        {status?.key_exists && (status.agent === 'none' ? <Chip kind="warn">ssh-agent not running</Chip> : status.in_agent ? <Chip kind="ok" icon={Unlock}>Unlocked</Chip> : <Chip kind="muted">Locked</Chip>)}
      </div>
      {perms.length > 0 && <div className="mt-3"><Banner kind="bad">{perms.join('; ')}. OpenSSH refuses a key others can read: use Fix permissions.</Banner></div>}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button small icon={KeyRound} disabled={Boolean(busy) || Boolean(status?.key_exists)} onClick={newKey}>New key</Button>
        <Button small icon={Upload} disabled={Boolean(busy) || !status?.key_exists || !settings.ssh_host} onClick={installKey}>Put key on server</Button>
        <Button small icon={Copy} disabled={Boolean(busy) || !status?.key_exists} onClick={copyLine}>Copy server line</Button>
        <Button small icon={Unlock} disabled={Boolean(busy) || !status?.key_exists || !status?.has_passphrase} onClick={unlock}>Unlock key</Button>
        <Button small icon={Lock} disabled={Boolean(busy) || !perms.length} onClick={fixPerms}>Fix permissions</Button>
      </div>
    </div>
  )
}

// The server's host key: confirmed once, checked strictly after; and a test of the whole connection
function HostSteps(props: { status: SshStatus | null; refresh: () => Promise<void> }): JSX.Element {
  const { status, refresh } = props
  const busy = useApp((s) => s.busy)
  const host = useApp((s) => s.settings.ssh_host)
  const checkHost = async (): Promise<void> => {
    const r = await useApp.getState().run('Check host key', ['ssh', 'scan', ...hostArgs()])
    if (!r.ok) return
    const keys = r.data.keys as HostKey[]
    if (r.data.state === 'trusted') {
      await ask.info('Host key confirmed', `${String(r.data.host)} offers the host key you confirmed before.`)
    } else {
      await confirmHost(String(r.data.host), keys, r.data.state === 'changed')
    }
    void refresh()
  }
  const test = async (): Promise<void> => {
    const s = useApp.getState().settings
    const args = ['ssh', 'test', ...hostArgs()]
    if (s.remote_root) args.push(`--remote-root=${String(s.remote_root)}`)
    await withHostTrust('Test connection', args)
    void refresh()
  }
  return (
    <div>
      <p className="mb-3 text-sm font-semibold text-slate-100">The server's host key</p>
      <div className="flex flex-wrap items-center gap-2">
        {status?.host_error && <Chip kind="bad" icon={ShieldAlert}>{status.host_error}</Chip>}
        {status?.host && (status.host_trusted ? <Chip kind="ok" icon={ShieldCheck}>Host key confirmed</Chip> : <Chip kind="warn">Host key not confirmed yet</Chip>)}
        {!status?.host && !status?.host_error && <span className="text-sm text-ink-400">Fill in the host under Where players get it.</span>}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button small icon={ShieldCheck} disabled={Boolean(busy) || !host} onClick={checkHost}>Check host key</Button>
        <Button small icon={Plug} disabled={Boolean(busy) || !host} onClick={test}>Test connection</Button>
      </div>
    </div>
  )
}

// Signing: the switch, the key's file, a new key, its public key; `chip`: the key's state beside the buttons (when
// the card's header does not show it)
function SigningSteps(props: { status: SignStatus | null; keep: (s: SignStatus | null) => void; chip?: boolean }): JSX.Element {
  const { status: keyStatus, keep } = props
  const busy = useApp((s) => s.busy)
  const keyPath = useApp((s) => String(s.effective('signing_key')))
  const [publicKey, setPublicKey] = useState('')

  const newKey = async (): Promise<void> => {
    const path = keyPath
    const p = await ask.passphrase('New signing key', `A passphrase for the new signing key at\n${path}\n\nIt protects the key if the file is ever copied. Back the key up: it cannot be made again.`, true, 10)
    if (p === null) return
    const r = await useApp.getState().run('New signing key', ['sign', 'keygen', `--path=${path}`, '--passphrase-stdin'], { passphrase: p })
    if (r.ok) {
      setPublicKey(String(r.data.public_key))
      useApp.getState().set('sign', true)
      keep(await signStatus(path))
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
    <div>
      <div className="grid gap-5">
        <SettingToggle setting="sign" label="Sign each release" hint="Players' updaters then refuse anything not signed by your key." />
        <Field label="Signing key" setting="signing_key" browse="file" mono hint={"The file's path is kept, never the key.\nReadable by your account only."} />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button small icon={KeyRound} disabled={Boolean(busy) || Boolean(keyStatus?.exists)} onClick={newKey}>New signing key</Button>
        <Button small icon={Copy} disabled={Boolean(busy) || !keyStatus?.exists} onClick={copyKey}>Copy public key</Button>
        {props.chip && (keyStatus?.exists
          ? <Chip kind="ok" icon={KeyRound}>Key ready{keyStatus.has_passphrase ? ', passphrase protected' : ''}</Chip>
          : <Chip kind="muted">No key yet</Chip>)}
      </div>
      {publicKey && <p className="selectable mt-4 break-all rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-xs text-delta-light">{publicKey}</p>}
    </div>
  )
}

// The one-time steps. Over SSH: "Set up your server" (the key, the host key, signing), folded by itself once they
// are all done, with a line that says where they stand. To a folder or this computer: signing alone, as "Sign
// releases (optional)".
function SetupCard(props: { mode: Mode }): JSX.Element {
  const sign = useApp((s) => Boolean(s.settings.sign))
  const sftp = props.mode === 'sftp'
  const [ssh, refresh] = useSshStatus(sftp)
  const [signing, keep] = useSignStatus()
  if (!sftp) {
    const state = signingSetup(sign, signing)
    return (
      <Card fold="release/sign" title="Sign releases (optional)" icon={PenLine} startsFolded={state.ready} summary={state.line}
        actions={signing?.exists ? <Chip kind="ok" icon={KeyRound}>Key ready{signing.has_passphrase ? ', passphrase protected' : ''}</Chip> : <Chip kind="muted">No key yet</Chip>}>
        <SigningSteps status={signing} keep={keep} />
      </Card>
    )
  }
  const state = serverSetup(ssh, sign, signing)
  const line = <span className={ssh ? (state.ready ? 'text-delta-light' : 'text-blob-light') : 'text-ink-400'}>{state.line}</span>
  return (
    <Card fold="release/setup" title="Set up your server" icon={ServerCog} startsFolded={state.ready} summary={line}
      info={'Done once for a server: an SSH key, on the server and unlocked; its host key, confirmed; and a signing key, if releases are signed.\nThis part folds by itself once everything in it is done.'}
      actions={<Button small variant="ghost" icon={RefreshCw} onClick={() => void refresh()}>Refresh</Button>}>
      <p className="text-sm">{line}</p>
      <div className="mt-5 space-y-6">
        <SshKeySteps status={ssh} refresh={refresh} />
        <div className="border-t border-ink-700/70 pt-5"><HostSteps status={ssh} refresh={refresh} /></div>
        <div className="border-t border-ink-700/70 pt-5">
          <p className="mb-3 text-sm font-semibold text-slate-100">Sign releases (optional)</p>
          <SigningSteps status={signing} keep={keep} chip />
        </div>
      </div>
    </Card>
  )
}

// the guide's part on statistics, on the wiki
const STATS_GUIDE = `${REPO_URL}/wiki/Publishing-a-release#statistics`
// the card's counting (store/stats.ts): each project's last statistics are kept while another page or project
// is shown, and a count's answer goes to the project it was asked for
let showStats: (card: StatsState) => void = () => undefined
const counter = statsCounter(
  (file) => useApp.getState().run('', ['stats', '--days=30', `--project=${file}`], { quiet: true }),
  () => useApp.getState().project,
  (card) => showStats(card)
)

function StatBars(props: { row: BarRow }): JSX.Element {
  const { row } = props
  return (
    <div>
      <div className="flex items-baseline justify-between text-[11px] text-ink-400">
        <span className="font-semibold uppercase tracking-wider">{row.label}</span>
        <span>{row.max ? `most in a day: ${row.max}` : 'none'}</span>
      </div>
      <div className="mt-1 flex h-10 items-end gap-[2px]">
        {row.bars.map((b) => (
          <div key={b.date} title={`${b.date}: ${b.value}`} className={`flex-1 rounded-sm ${b.value ? 'bg-delta/80' : 'bg-ink-700'}`}
            style={{ height: b.value ? `${Math.max(8, Math.round(b.height * 100))}%` : '2px' }} />
        ))}
      </div>
    </div>
  )
}

// Statistics: the current release and what it changed, the store, and from the web server's access log the
// update checks, downloads and players of each day and the releases players are on. Counted when a project is
// first shown, after a release, when the access log changes, and on Refresh. Folded, it says the checks and
// players of the period.
function StatsCard(): JSX.Element {
  const project = useApp((s) => s.project)
  const accessLog = useApp((s) => String(s.settings.access_log ?? ''))
  const mode = useApp((s) => modeOf(s.settings.deploy_mode))
  const released = useApp((s) => s.results.release as Data | undefined)
  const [card, setCard] = useState<StatsState>(counter.shown(project))
  const { view, loading, error } = card
  const load = useCallback(() => counter.count(), [])

  useEffect(() => {
    showStats = setCard
    return () => {
      showStats = () => undefined
    }
  }, [])
  useEffect(() => {
    setCard(counter.shown(project))
    if (!counter.kept.has(project)) void load()
  }, [project, load])
  // a release made while it is shown, or another access log typed: counted again
  const seen = useRef({ project, released, accessLog })
  useEffect(() => {
    const before = seen.current
    seen.current = { project, released, accessLog }
    if (before.project !== project) return
    if (released && before.released !== released) {
      void load()
      return
    }
    if (before.accessLog === accessLog) return
    const t = setTimeout(() => void load(), 1500)
    return () => clearTimeout(t)
  }, [project, released, accessLog, load])

  const dl = view?.downloads
  return (
    <Card fold="release/stats" title="Statistics" icon={ChartColumn} summary={statsSummary(card, accessLog)}
      actions={<Button small variant="ghost" icon={RefreshCw} disabled={loading} onClick={() => void load()}>{loading ? 'Counting...' : 'Refresh'}</Button>}>
      {error && <div className="mb-4"><Banner kind="bad">{error}</Banner></div>}
      {!view && !error && <p className="text-sm text-ink-400">Counting...</p>}
      {view?.release ? (
        <div>
          <p className="text-sm text-slate-100">
            Release <span className="font-semibold">{view.release.version}</span>
            {view.release.name ? ` of ${view.release.name}` : ''}, published {when(view.release.published)}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Files" value={view.release.files} />
            <Stat label="Size" value={human(view.release.bytes)} />
            <Stat label="Distinct files" value={view.release.blobs} />
            <Stat label="The store" value={human(view.store?.bytes ?? 0)} />
          </div>
          <p className="hint">
            {(view.change
              ? `From ${view.change.from}: ${view.change.new} new, ${view.change.changed} changed, ${view.change.removed} removed.\n` +
                `A player on ${view.change.from} downloads ${human(view.change.download)}.\n`
              : 'The first release in this store.\n') +
              `${view.store?.releases === 1 ? '1 release' : `${view.store?.releases ?? 0} releases`} in its history; ` +
              `the store keeps ${view.store?.files ?? 0} files.`}
          </p>
        </div>
      ) : (
        view && <p className="hint">{sentences(view.releasesProblem) || 'Nothing is released yet.'}</p>
      )}
      <div className="mt-5 border-t border-ink-700/70 pt-4">
        <p className="text-sm font-semibold text-slate-100">Players, the last {view?.days || 30} days</p>
        {dl ? (
          <>
            <div className="mt-3 grid grid-cols-3 gap-3">
              <Stat label="Update checks" value={dl.total.checks} />
              <Stat label="Downloads" value={`${dl.total.downloads} (${human(dl.total.bytes)})`} />
              <Stat label="Players" value={players(dl.total.players, dl.about)} accent />
            </div>
            <div className="mt-4 space-y-3">
              {dl.rows.map((row) => <StatBars key={row.key} row={row} />)}
              <div className="flex justify-between text-[11px] text-ink-400"><span>{dl.first}</span><span>{dl.last}</span></div>
            </div>
            {dl.releases.length > 0 && (
              <div className="mt-4">
                <p className="label">The releases players are on{dl.about ? ' (about)' : ''}</p>
                <div className="space-y-1.5">
                  {dl.releases.map((r) => (
                    <div key={r.key} className="flex items-center gap-3 text-sm">
                      <span className={`w-28 truncate text-slate-100 ${r.key.startsWith('release:') ? 'font-mono' : 'italic'}`} title={r.label}>{r.label}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-ink-700">
                        <div className="h-full rounded-full bg-delta" style={{ width: `${Math.round(r.share * 100)}%` }} />
                      </div>
                      <span className="w-24 text-right tabular-nums text-ink-300">{r.players === 1 ? '1 player' : `${r.players} players`}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <p className="hint">
              {`Counted from ${dl.log}, ${dl.readFrom === 'server' ? 'on the server' : 'on this computer'}.\nCounts only: never an address.` +
                (dl.about ? '\nSo many players that they are estimated: the counts of players are about right.' : '') +
                (dl.unreadable ? `\n${dl.unreadable} of ${dl.lines} lines could not be read.` : '')}
            </p>
          </>
        ) : !accessLog ? (
          <p className="mt-2 whitespace-pre-line text-sm text-ink-300">
            {"Name the web server's access log below to see update checks, downloads and players per day, and the releases players are on.\nCounts only: never an address."}
          </p>
        ) : view?.downloadsProblem ? (
          <div className="mt-3"><Banner kind="warn">{view.downloadsProblem}</Banner></div>
        ) : null}
        <Field className="mt-4" label="Access log" setting="access_log" mono
          placeholder={mode === 'sftp' ? '/var/log/nginx/access.log' : 'the log file on this computer'}
          hint={(mode === 'sftp' ? 'Its path on the server: read over SFTP, like a release is sent.' : 'Its path on this computer.') +
            "\nnginx's or Apache's access log, or Caddy's JSON log."} />
        {!accessLog && (
          <a href={STATS_GUIDE} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-delta-light hover:underline">
            How to set up statistics: the publishing guide
          </a>
        )}
      </div>
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
        <p className="hint">Fill in Players download from, under Where players get it: the address players use then shows here.</p>
      )}
    </div>
  )
}

// A project with no folder to release yet: a short start, the folder to release; the whole page is one click away
function ReleaseStart(props: { onWhole: () => void }): JSX.Element {
  return (
    <div className="space-y-5">
      <Card fold="release/start" title="Publish this project" icon={Rocket}>
        <p className="text-sm text-ink-300">Pick the folder to release: the rest of the page comes once it is picked.</p>
        <Field className="mt-4" label="Folder to release" setting="publish_src" browse="folder" mono
          hint={'The files exactly as players should have them.\nAny type: zip, pk3, textures, programs.'} />
        <div className="mt-5">
          <Button variant="ghost" icon={ChevronsDown} onClick={props.onWhole}>Show the whole page</Button>
        </div>
      </Card>
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
  const project = useApp((s) => s.project)
  const [dry, setDry] = useState(false)
  // the whole page shown for a project with no folder to release yet (until another project is shown)
  const [whole, setWhole] = useState(false)
  useEffect(() => setWhole(false), [project])
  const mode = modeOf(settings.deploy_mode)

  if (!settings.publish_src && !whole) return <ReleaseStart onWhole={() => setWhole(true)} />

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
      <Card fold="release/what" title="What to release" icon={Package}>
        <div className="grid gap-5 md:grid-cols-2">
          <Field className="md:col-span-2" label="Folder to release" setting="publish_src" browse="folder" mono
            hint={'The files exactly as players should have them.\nAny type: zip, pk3, textures, programs.'} />
          <Field label="Name" setting="name" placeholder="My Game" />
          <Field label="Version" setting="version" placeholder="1.0"
            hint={'Shown to players.\nA new version or the same one: either way only what changed is sent.'} />
          <PathList setting="leave_out" label="Leave out of the release" empty="Nothing picked: the whole folder is released."
            hint={'Players never get these. A folder leaves out everything in it.\nAlways left out: .git, .svn, .hg, __pycache__, Thumbs.db, desktop.ini, .DS_Store, *.tmp.'} />
          <PathList setting="protect" label="Players keep their own copy" empty="Nothing picked: every file follows the release."
            hint={'Sent once, then never overwritten: players keep their changes (settings, saves).\nA folder covers everything in it.'} />
        </div>
      </Card>

      <Card fold="release/where" title="Where players get it" icon={Globe}>
        <Segmented value={mode} onChange={(v) => set('deploy_mode', v)}
          options={[
            { value: 'sftp', label: 'Your server (SSH)', icon: ServerIcon },
            { value: 'folder', label: 'A folder', icon: HardDrive },
            { value: 'local', label: 'This computer, to test', icon: Monitor }
          ]} />
        <div className="mt-5">
          {mode === 'sftp' && (
            <div className="space-y-5">
              <div className="grid gap-5 md:grid-cols-6">
                <Field className="md:col-span-3" label="Host" setting="ssh_host" placeholder="game.example.com" mono />
                <Field className="md:col-span-2" label="User" setting="ssh_user" placeholder="deploy" mono />
                <Field className="md:col-span-1" label="Port" setting="ssh_port" type="number" />
                <Field className="md:col-span-3" label="Remote folder" setting="remote_root" placeholder="/var/www/game" mono
                  hint={'The folder on the server that its web server serves.\nForward slashes, even on a Windows server.'} />
                <Field className="md:col-span-3" label="Players download from" setting="public_url" placeholder="https://game.example.com/" mono
                  hint={'The address the web server gives that folder.\nPlayers put it on their Update page.'} />
              </div>
              <SettingToggle setting="htaccess" label=".htaccess for Apache" hint="Serve only the release: no listings, the right cache headers." />
            </div>
          )}
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
                Release serves it at <span className="font-mono">http://localhost:{port}/</span>, for this computer only.
                <br />
                Put that address on the Update page to try the release before players get it.
              </p>
            </div>
          )}
        </div>
      </Card>

      <SetupCard mode={mode} />

      <Card fold="release/send" title="Send to players" icon={Rocket}>
        <p className="text-sm text-ink-300">
          {mode === 'local'
            ? `Serves the release at ${where}, on this computer only.`
            : `Sends only what changed to ${where}.`}
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

      <StatsCard />
    </div>
  )
}
