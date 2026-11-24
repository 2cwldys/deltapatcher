// The building blocks every page uses.
import { ReactNode, useState } from 'react'
import { FolderOpen, FileSearch, LucideIcon } from 'lucide-react'
import { useApp } from '../store/app'

export function Card(props: { title?: string; icon?: LucideIcon; actions?: ReactNode; children: ReactNode; className?: string }): JSX.Element {
  const Icon = props.icon
  return (
    <section className={`rounded-xl border border-ink-700 bg-ink-850/80 shadow-lg shadow-black/20 ${props.className ?? ''}`}>
      {(props.title || props.actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-ink-700/70 px-5 py-3.5">
          <h2 className="flex items-center gap-2.5 text-sm font-semibold text-slate-100">
            {Icon && <Icon size={16} className="text-delta" />}
            {props.title}
          </h2>
          {props.actions && <div className="flex items-center gap-2">{props.actions}</div>}
        </header>
      )}
      <div className="p-5">{props.children}</div>
    </section>
  )
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'remove'
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-delta text-ink-950 hover:bg-delta-light font-semibold shadow-md shadow-delta/20',
  secondary: 'bg-ink-700 text-slate-100 hover:bg-ink-600 border border-ink-600',
  ghost: 'text-ink-300 hover:text-slate-100 hover:bg-ink-700/60',
  danger: 'bg-rose-600/90 text-white hover:bg-rose-500 font-semibold',
  // a quiet button that removes something: red when the pointer is on it
  remove: 'text-ink-300 border border-ink-600 hover:border-rose-500/50 hover:bg-rose-500/10 hover:text-rose-300'
}

export function Button(props: {
  variant?: Variant
  icon?: LucideIcon
  children?: ReactNode
  onClick?: () => void
  disabled?: boolean
  title?: string
  small?: boolean
}): JSX.Element {
  const Icon = props.icon
  return (
    <button
      type="button"
      title={props.title}
      disabled={props.disabled}
      onClick={props.onClick}
      className={`inline-flex items-center justify-center gap-2 rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        props.small ? 'px-2.5 py-1.5 text-xs' : 'px-4 py-2 text-sm'
      } ${VARIANTS[props.variant ?? 'secondary']}`}
    >
      {Icon && <Icon size={props.small ? 14 : 16} />}
      {props.children}
    </button>
  )
}

// a text field bound to a setting in deltapatcher.ini
export function Field(props: {
  label: string
  setting: string
  hint?: ReactNode
  placeholder?: string
  browse?: 'folder' | 'file'
  mono?: boolean
  type?: 'text' | 'number'
  className?: string
  // what is wrong with a typed value ('' when nothing): such a value stays on screen, unsaved, and the hint
  // says what is wrong
  check?: (v: string) => string
}): JSX.Element {
  const value = useApp((s) => s.settings[props.setting])
  const fallback = useApp((s) => s.schema.find((x) => x.key === props.setting)?.default)
  const set = useApp((s) => s.set)
  const text = value === undefined || value === null ? '' : String(value)
  const [draft, setDraft] = useState<string | null>(null)   // a value not saved while it is not right
  const problem = draft !== null && props.check ? props.check(draft) : ''
  const placeholder = props.placeholder ?? (fallback !== undefined && fallback !== '' ? String(fallback) : undefined)
  const browse = async (): Promise<void> => {
    const p = props.browse === 'file' ? await window.api.pickFile(text) : await window.api.pickFolder(text)
    if (p) set(props.setting, p)
  }
  return (
    <div className={props.className}>
      <label className="label">{props.label}</label>
      <div className="flex gap-2">
        <input
          className={`input ${props.mono ? 'font-mono text-[13px]' : ''}`}
          value={draft ?? text}
          placeholder={placeholder}
          inputMode={props.type === 'number' ? 'numeric' : undefined}
          spellCheck={false}
          onChange={(e) => {
            const v = e.target.value
            if (props.check) {
              if (props.check(v)) return setDraft(v)
              setDraft(null)
              return set(props.setting, v)
            }
            if (props.type !== 'number') return set(props.setting, v)
            // whole numbers only: anything else stays on screen until it is one, and is never saved as 0
            if (/^\d{1,6}$/.test(v)) {
              setDraft(null)
              set(props.setting, Number(v))
            } else {
              setDraft(v)
            }
          }}
          onBlur={() => setDraft(null)}
        />
        {props.browse && (
          <Button icon={props.browse === 'file' ? FileSearch : FolderOpen} onClick={browse} title="Browse">
            Browse
          </Button>
        )}
      </div>
      {problem ? <p className="hint text-rose-300">{problem}</p> : props.hint && <p className="hint">{props.hint}</p>}
    </div>
  )
}

export function Toggle(props: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }): JSX.Element {
  return (
    <label className={`flex items-start gap-3 ${props.disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}>
      <button
        type="button"
        role="switch"
        aria-checked={props.checked}
        disabled={props.disabled}
        onClick={() => props.onChange(!props.checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors ${props.checked ? 'bg-delta' : 'bg-ink-600'} ${props.disabled ? 'opacity-50' : ''}`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${props.checked ? 'left-[18px]' : 'left-0.5'}`}
        />
      </button>
      <span>
        <span className="block text-sm text-slate-100">{props.label}</span>
        {props.hint && <span className="block whitespace-pre-line text-pretty text-xs text-ink-400">{props.hint}</span>}
      </span>
    </label>
  )
}

// blocked: the setting cannot work here; the switch shows off and the hint says why
export function SettingToggle(props: { setting: string; label: string; hint?: string; blocked?: string }): JSX.Element {
  const value = useApp((s) => Boolean(s.settings[props.setting]))
  const set = useApp((s) => s.set)
  return props.blocked ? (
    <Toggle checked={false} disabled onChange={() => undefined} label={props.label} hint={props.blocked} />
  ) : (
    <Toggle checked={value} onChange={(v) => set(props.setting, v)} label={props.label} hint={props.hint} />
  )
}

type ChipKind = 'ok' | 'warn' | 'bad' | 'info' | 'muted'
const CHIPS: Record<ChipKind, string> = {
  ok: 'bg-delta/15 text-delta-light border-delta/30',
  warn: 'bg-blob/15 text-blob-light border-blob/30',
  bad: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
  info: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  muted: 'bg-ink-700/60 text-ink-300 border-ink-600'
}

export function Chip(props: { kind: ChipKind; children: ReactNode; icon?: LucideIcon }): JSX.Element {
  const Icon = props.icon
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${CHIPS[props.kind]}`}>
      {Icon && <Icon size={12} />}
      {props.children}
    </span>
  )
}

export function Stat(props: { label: string; value: ReactNode; accent?: boolean }): JSX.Element {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900/70 px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-ink-400">{props.label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${props.accent ? 'text-delta-light' : 'text-slate-100'}`}>{props.value}</div>
    </div>
  )
}

export function Segmented<T extends string>(props: { value: T; options: { value: T; label: string; icon?: LucideIcon }[]; onChange: (v: T) => void }): JSX.Element {
  return (
    <div className="inline-flex rounded-lg border border-ink-600 bg-ink-900 p-1">
      {props.options.map((o) => {
        const Icon = o.icon
        const on = o.value === props.value
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => props.onChange(o.value)}
            className={`inline-flex items-center gap-2 rounded-md px-3.5 py-1.5 text-sm transition-colors ${
              on ? 'bg-ink-700 text-slate-100 shadow' : 'text-ink-300 hover:text-slate-100'
            }`}
          >
            {Icon && <Icon size={15} className={on ? 'text-delta' : ''} />}
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

export function Banner(props: { kind: 'warn' | 'info' | 'bad'; children: ReactNode }): JSX.Element {
  const cls =
    props.kind === 'warn'
      ? 'border-blob/30 bg-blob/10 text-blob-light'
      : props.kind === 'bad'
        ? 'border-rose-500/30 bg-rose-500/10 text-rose-200'
        : 'border-sky-500/30 bg-sky-500/10 text-sky-200'
  return <div className={`rounded-lg border px-4 py-3 text-sm leading-relaxed ${cls}`}>{props.children}</div>
}

export function human(n: number): string {
  if (n < 1024) return `${n} bytes`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n
  let u = -1
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024
    u++
  }
  return `${v.toFixed(1)} ${units[u]}`
}
