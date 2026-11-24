// The dialogs: confirm, passphrase, host keys, information. Passphrases live only in this component's
// state until they are handed to the engine, then they are cleared.
import { useEffect, useState } from 'react'
import { AlertTriangle, Copy, KeyRound, ShieldCheck } from 'lucide-react'
import { useModal } from '../store/modal'
import { Button } from './ui'

function Frame(props: { title: string; icon?: JSX.Element; children: React.ReactNode; footer: React.ReactNode }): JSX.Element {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-xl border border-ink-600 bg-ink-850 shadow-2xl shadow-black/50">
        <div className="flex items-center gap-3 border-b border-ink-700 px-6 py-4">
          {props.icon}
          <h3 className="text-base font-semibold">{props.title}</h3>
        </div>
        <div className="px-6 py-5 text-sm leading-relaxed text-ink-300">{props.children}</div>
        <div className="flex justify-end gap-2 border-t border-ink-700 px-6 py-4">{props.footer}</div>
      </div>
    </div>
  )
}

export default function Modals(): JSX.Element | null {
  const spec = useModal((s) => s.spec)
  const close = useModal((s) => s.close)
  const [p1, setP1] = useState('')
  const [p2, setP2] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    setP1('')
    setP2('')
    setErr('')
  }, [spec])

  if (!spec) return null

  if (spec.kind === 'confirm') {
    return (
      <Frame title={spec.title} icon={<AlertTriangle size={18} className={spec.danger ? 'text-rose-400' : 'text-blob'} />}
        footer={
          <>
            <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
            <Button variant={spec.danger ? 'danger' : 'primary'} onClick={() => close(true)}>{spec.confirm}</Button>
          </>
        }>
        <p className="whitespace-pre-wrap">{spec.body}</p>
      </Frame>
    )
  }

  if (spec.kind === 'info') {
    return (
      <Frame title={spec.title} icon={<ShieldCheck size={18} className="text-delta" />}
        footer={
          <>
            {spec.code && <Button icon={Copy} onClick={() => window.api.copy(spec.code ?? '')}>Copy</Button>}
            <Button variant="primary" onClick={() => close(undefined)}>OK</Button>
          </>
        }>
        <p className="whitespace-pre-wrap">{spec.body}</p>
        {spec.code && <pre className="selectable mt-3 overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-ink-700 bg-ink-950 p-3 font-mono text-xs text-violet-300">{spec.code}</pre>}
      </Frame>
    )
  }

  if (spec.kind === 'hostkeys') {
    return (
      <Frame title={`First connection to ${spec.host}`} icon={<ShieldCheck size={18} className="text-blob" />}
        footer={
          <>
            <Button variant="ghost" onClick={() => close(false)}>Do not trust</Button>
            <Button variant="primary" onClick={() => close(true)}>They match: trust this server</Button>
          </>
        }>
        <p>The server offers these host keys. Compare them with the fingerprints your hosting provider shows, or run this on the server:</p>
        <pre className="selectable mt-2 rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-xs text-violet-300">ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</pre>
        <div className="mt-3 space-y-1.5">
          {spec.keys.map((k) => (
            <div key={k.fingerprint} className="selectable flex gap-3 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-xs">
              <span className="w-28 shrink-0 text-ink-400">{k.type}</span>
              <span className="break-all text-violet-300">{k.fingerprint}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs">Trust it only if they match. DeltaPatcher remembers this server and refuses it if its key ever changes.</p>
      </Frame>
    )
  }

  // passphrase
  const submit = (): void => {
    if (!p1 && !spec.allowEmpty) return setErr('Type the passphrase.')
    if (p1 && p1.length < spec.minLength) return setErr(`Use at least ${spec.minLength} characters (a few words work well).`)
    if (spec.twice && p1 !== p2) return setErr('The two passphrases differ.')
    const v = p1
    setP1('')
    setP2('')
    close(v)
  }
  return (
    <Frame title={spec.title} icon={<KeyRound size={18} className="text-delta" />}
      footer={
        <>
          <Button variant="ghost" onClick={() => close(null)}>Cancel</Button>
          <Button variant="primary" onClick={submit}>OK</Button>
        </>
      }>
      <p className="whitespace-pre-wrap">{spec.body}</p>
      <input autoFocus type="password" className="input mt-4" placeholder="Passphrase" value={p1}
        onChange={(e) => setP1(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && !spec.twice && submit()} />
      {spec.twice && (
        <input type="password" className="input mt-2" placeholder="The same passphrase again" value={p2}
          onChange={(e) => setP2(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} />
      )}
      {err && <p className="mt-2 text-xs text-rose-300">{err}</p>}
      <p className="mt-3 text-xs text-ink-400">It goes straight to DeltaPatcher's engine and is not saved anywhere.</p>
    </Frame>
  )
}
