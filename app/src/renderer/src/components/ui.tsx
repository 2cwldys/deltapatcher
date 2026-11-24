// The building blocks every page uses.
import { KeyboardEvent as ReactKeyboardEvent, ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, ChevronUp, Ellipsis, FolderOpen, FileSearch, Info, LucideIcon } from 'lucide-react'
import { useApp } from '../store/app'
import { useFold } from '../store/folds'

// A box of a page. Every card folds: the arrow at the right of its header (UP while it is folded, DOWN while it
// is open) or a click on its title folds or unfolds it, and the window remembers it by `fold` ('page/card'). A
// folded card keeps its header (title, marks, header buttons) and shows `summary`, a line, when it has one.
export function Card(props: {
  title: string
  fold: string
  icon?: LucideIcon
  // beside the title: small marks (selected, a chip)
  marks?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
  // folded until it is opened by hand (the server setup, once it is all done); every other card starts open
  startsFolded?: boolean
  summary?: ReactNode
  // an (i) beside the title, with this text
  info?: string
  tone?: 'danger'
  // the title's own tooltip (a project's settings file)
  titleHint?: string
  // a ring around it (the project shown on the other pages)
  ring?: boolean
  // its id on the page, for a link that leads to it
  anchor?: string
}): JSX.Element {
  const [folded, setFolded] = useFold(props.fold, props.startsFolded)
  const body = useId()
  const Icon = props.icon
  const danger = props.tone === 'danger'
  const toggle = (): void => setFolded(!folded)
  const frame = danger ? 'border-rose-500/30 bg-rose-500/5' : props.ring ? 'border-delta/40 bg-ink-850/80 ring-1 ring-delta/30' : 'border-ink-700 bg-ink-850/80'
  const line = danger ? 'border-rose-500/20' : 'border-ink-700/70'
  return (
    <section id={props.anchor} className={`rounded-xl border shadow-lg shadow-black/20 ${frame} ${props.className ?? ''}`}>
      <header className="flex min-h-[3.25rem] items-center gap-3 px-5 py-2.5">
        <h2 className="flex min-w-0 items-center gap-2.5 text-sm font-semibold text-slate-100">
          <button type="button" onClick={toggle} aria-expanded={!folded} aria-controls={body} title={props.titleHint}
            className="flex min-w-0 items-center gap-2.5 rounded text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-delta/40">
            {Icon && <Icon size={16} className={`shrink-0 ${danger ? 'text-rose-300' : 'text-delta'}`} />}
            <span className="truncate">{props.title}</span>
          </button>
          {props.info && <InfoTip text={props.info} />}
          {props.marks}
        </h2>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {props.actions}
          <button type="button" onClick={toggle} aria-expanded={!folded} aria-controls={body} title={folded ? 'Unfold' : 'Fold'}
            className="rounded-md p-1 text-ink-400 transition-colors hover:bg-ink-700/60 hover:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-delta/40">
            {folded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
      </header>
      {folded
        ? props.summary && <div className={`border-t px-5 py-2.5 text-xs leading-relaxed text-ink-300 ${line}`}>{props.summary}</div>
        : <div id={body} className={`border-t p-5 ${line}`}>{props.children}</div>}
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

function buttonClass(variant: Variant | undefined, small: boolean | undefined): string {
  return `inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-delta/40 disabled:cursor-not-allowed disabled:opacity-40 ${
    small ? 'px-2.5 py-1.5 text-xs' : 'px-4 py-2 text-sm'
  } ${VARIANTS[variant ?? 'secondary']}`
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
    <button type="button" title={props.title} disabled={props.disabled} onClick={props.onClick} className={buttonClass(props.variant, props.small)}>
      {Icon && <Icon size={props.small ? 14 : 16} />}
      {props.children}
    </button>
  )
}

// Something floating over the window at a place on screen (a tooltip, a menu): it closes when the window scrolls
// (anything but itself) or changes size, on Escape, and when Force stop comes on.
function useFloating(open: boolean, close: () => void, own: React.RefObject<HTMLElement>): void {
  const stopped = useApp((s) => s.stopped)
  useEffect(() => {
    if (stopped && open) close()
  }, [stopped, open, close])
  useEffect(() => {
    if (!open) return
    const scrolled = (e: Event): void => {
      if (!(e.target instanceof Node) || !own.current?.contains(e.target)) close()
    }
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('scroll', scrolled, true)
    window.addEventListener('resize', close)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('scroll', scrolled, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('keydown', key)
    }
  }, [open, close, own])
}

// A small (i) beside a label or a title: the whole explanation, while the pointer is on it, while it has the
// focus, or after a click (a second click closes it). The text keeps its line breaks.
export function InfoTip(props: { text: string }): JSX.Element {
  const icon = useRef<HTMLButtonElement>(null)
  const tip = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [at, setAt] = useState({ left: -9999, top: -9999 })
  const close = useRef(() => {
    setOpen(false)
    setPinned(false)
  }).current
  useFloating(open, close, tip)
  // under the (i), or over it when there is no room below; never past the window's edges
  useLayoutEffect(() => {
    if (!open || !icon.current || !tip.current) return
    const r = icon.current.getBoundingClientRect()
    const w = tip.current.offsetWidth
    const h = tip.current.offsetHeight
    const left = Math.max(8, Math.min(r.left - 12, window.innerWidth - w - 8))
    const below = r.bottom + 6
    const top = below + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 6) : below
    setAt({ left, top })
  }, [open, props.text])
  return (
    <>
      <button ref={icon} type="button" aria-label="More about it" aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => !pinned && setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          setOpen(false)
          setPinned(false)
        }}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          if (pinned) close()
          else {
            setOpen(true)
            setPinned(true)
          }
        }}
        className="inline-flex shrink-0 items-center justify-center rounded-full align-middle normal-case tracking-normal text-ink-400 transition-colors hover:text-delta-light focus:outline-none focus-visible:ring-2 focus-visible:ring-delta/40">
        <Info size={14} />
      </button>
      {open && createPortal(
        <div ref={tip} role="tooltip" style={{ left: at.left, top: at.top }}
          className="pointer-events-none fixed z-[70] max-w-sm whitespace-pre-line rounded-lg border border-ink-600 bg-ink-800 px-3 py-2 text-xs font-normal normal-case leading-relaxed tracking-normal text-slate-200 shadow-xl shadow-black/50">
          {props.text}
        </div>,
        document.body
      )}
    </>
  )
}

export interface MenuItem {
  label: string
  icon?: LucideIcon
  onClick: () => void
  disabled?: boolean
  title?: string
  // red: it removes something
  danger?: boolean
}

// A button that opens a short list of actions over the window ("..." when it has no label of its own).
export function Menu(props: { items: MenuItem[]; title: string; label?: string; icon?: LucideIcon; small?: boolean; variant?: Variant; disabled?: boolean }): JSX.Element {
  const trigger = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState<{ left: number; top?: number; bottom?: number }>({ left: 0, top: 0 })
  const close = useRef(() => setOpen(false)).current
  useFloating(open, close, list)
  const Icon = props.icon ?? (props.label ? undefined : Ellipsis)
  const WIDTH = 224

  const toggle = (): void => {
    const r = trigger.current?.getBoundingClientRect()
    if (open || !r) return setOpen(false)
    const left = Math.max(8, Math.min(r.right - WIDTH, window.innerWidth - WIDTH - 8))
    const tall = props.items.length * 36 + 10
    setAt(r.bottom + 4 + tall > window.innerHeight - 8 && r.top - 4 - tall > 8 ? { left, bottom: window.innerHeight - r.top + 4 } : { left, top: r.bottom + 4 })
    setOpen(true)
  }
  // the first action takes the focus when the list opens; the arrow keys move it, Tab closes the list
  useEffect(() => {
    if (open) list.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
  }, [open])
  const keys = (e: ReactKeyboardEvent): void => {
    const items = [...(list.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
    const i = items.indexOf(document.activeElement as HTMLButtonElement)
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus()
    } else if (e.key === 'Tab') {
      setOpen(false)
    } else if (e.key === 'Escape') {
      trigger.current?.focus()
    }
  }

  return (
    <>
      <button ref={trigger} type="button" title={props.title} aria-label={props.label ? undefined : props.title} aria-haspopup="menu"
        aria-expanded={open} disabled={props.disabled} onClick={toggle}
        className={buttonClass(props.variant, props.small) + (props.label ? '' : props.small ? ' !px-1.5' : ' !px-2.5')}>
        {Icon && <Icon size={props.small ? 14 : 16} />}
        {props.label}
        {props.label && <ChevronDown size={props.small ? 13 : 14} className="-mr-1 opacity-70" />}
      </button>
      {open && createPortal(
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div ref={list} role="menu" onKeyDown={keys} style={{ left: at.left, top: at.top, bottom: at.bottom, width: WIDTH }}
            className="fixed z-40 rounded-lg border border-ink-600 bg-ink-850 py-1 shadow-xl shadow-black/50">
            {props.items.map((item) => {
              const ItemIcon = item.icon
              return (
                <button key={item.label} type="button" role="menuitem" disabled={item.disabled} title={item.title}
                  onClick={() => {
                    setOpen(false)
                    item.onClick()
                  }}
                  className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors focus:outline-none disabled:cursor-not-allowed disabled:opacity-40 ${
                    item.danger ? 'text-ink-300 hover:bg-rose-500/10 hover:text-rose-300 focus:bg-rose-500/10 focus:text-rose-300' : 'text-ink-300 hover:bg-ink-700/60 hover:text-slate-100 focus:bg-ink-700/60 focus:text-slate-100'
                  }`}>
                  {ItemIcon && <ItemIcon size={14} className={`shrink-0 ${item.danger ? '' : 'text-delta-light'}`} />}
                  {item.label}
                </button>
              )
            })}
          </div>
        </>,
        document.body
      )}
    </>
  )
}

// the first line of an explanation: what shows under a field, the whole of it being behind the (i)
export function firstLine(text: string | undefined): string {
  return (text ?? '').split('\n')[0]
}

// A label, the setting's own name small beside it (the Settings page), and an (i) with the whole explanation
// when there is more of it than the line under the field shows.
function FieldLabel(props: { label: string; setting?: string; info?: string }): JSX.Element {
  return (
    <div className="mb-1.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      <label className="label !mb-0">{props.label}</label>
      {props.setting && <span className="font-mono text-[11px] text-ink-400">{props.setting}</span>}
      {props.info && <InfoTip text={props.info} />}
    </div>
  )
}

// the line under a field and the (i)'s text: the line given, or the explanation's first line; the (i) holds the
// whole explanation when there is more of it
function lineAndInfo(hint: string | undefined, line: string | undefined): [string, string] {
  const shown = line ?? firstLine(hint)
  return [shown, hint && hint !== shown ? hint : '']
}

// a text field bound to a setting in deltapatcher.ini
export function Field(props: {
  label: string
  setting: string
  // the explanation: its first line shows under the field, the whole of it behind the (i) beside the label
  hint?: string
  // the line under the field instead of the explanation's first line
  line?: string
  // the setting's own name, small beside the label
  showSetting?: boolean
  placeholder?: string
  browse?: 'folder' | 'file'
  mono?: boolean
  type?: 'text' | 'number'
  className?: string
  // what is wrong with a typed value ('' when nothing): such a value stays on screen, unsaved, and the line
  // under the field says what is wrong
  check?: (v: string) => string
}): JSX.Element {
  const value = useApp((s) => s.settings[props.setting])
  const fallback = useApp((s) => s.schema.find((x) => x.key === props.setting)?.default)
  const set = useApp((s) => s.set)
  const text = value === undefined || value === null ? '' : String(value)
  const [draft, setDraft] = useState<string | null>(null)   // a value not saved while it is not right
  const problem = draft !== null && props.check ? props.check(draft) : ''
  const placeholder = props.placeholder ?? (fallback !== undefined && fallback !== '' ? String(fallback) : undefined)
  const [line, info] = lineAndInfo(props.hint, props.line)
  const browse = async (): Promise<void> => {
    const p = props.browse === 'file' ? await window.api.pickFile(text) : await window.api.pickFolder(text)
    if (p) set(props.setting, p)
  }
  return (
    <div className={`min-w-0 ${props.className ?? ''}`}>
      <FieldLabel label={props.label} setting={props.showSetting ? props.setting : undefined} info={info} />
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
              return set(props.setting, props.type === 'number' ? Number(v) : v)
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
      {problem ? <p className="hint text-rose-300">{problem}</p> : line && <p className="hint">{line}</p>}
    </div>
  )
}

export function Toggle(props: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  // the explanation: its first line shows under the label, the whole of it behind the (i)
  hint?: string
  line?: string
  // the setting's own name, small beside the label
  setting?: string
  disabled?: boolean
}): JSX.Element {
  const [line, info] = lineAndInfo(props.hint, props.line)
  return (
    <div className={`flex items-start gap-3 ${props.disabled ? 'cursor-not-allowed' : ''}`}>
      <button
        type="button"
        role="switch"
        aria-checked={props.checked}
        aria-label={props.label}
        disabled={props.disabled}
        onClick={() => props.onChange(!props.checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors ${props.checked ? 'bg-delta' : 'bg-ink-600'} ${props.disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${props.checked ? 'left-[18px]' : 'left-0.5'}`}
        />
      </button>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className={`text-sm text-slate-100 ${props.disabled ? '' : 'cursor-pointer'}`} onClick={() => !props.disabled && props.onChange(!props.checked)}>
            {props.label}
          </span>
          {props.setting && <span className="font-mono text-[11px] text-ink-400">{props.setting}</span>}
          {info && <InfoTip text={info} />}
        </div>
        {line && <span className="block whitespace-pre-line text-pretty text-xs text-ink-400">{line}</span>}
      </div>
    </div>
  )
}

// blocked: the setting cannot work here; the switch shows off and the line says why
export function SettingToggle(props: { setting: string; label: string; hint?: string; line?: string; showSetting?: boolean; blocked?: string }): JSX.Element {
  const value = useApp((s) => Boolean(s.settings[props.setting]))
  const set = useApp((s) => s.set)
  const own = props.showSetting ? props.setting : undefined
  return props.blocked ? (
    <Toggle checked={false} disabled onChange={() => undefined} label={props.label} hint={props.blocked} setting={own} />
  ) : (
    <Toggle checked={value} onChange={(v) => set(props.setting, v)} label={props.label} hint={props.hint} line={props.line} setting={own} />
  )
}

// a setting that takes one of a few words (torrents: allow or refuse), as buttons side by side, never typed
export function SettingChoice(props: {
  setting: string
  label: string
  options: { value: string; label: string }[]
  hint?: string
  line?: string
  showSetting?: boolean
}): JSX.Element {
  const value = useApp((s) => String(s.settings[props.setting] ?? s.schema.find((x) => x.key === props.setting)?.default ?? ''))
  const set = useApp((s) => s.set)
  const [line, info] = lineAndInfo(props.hint, props.line)
  return (
    <div className="min-w-0">
      <FieldLabel label={props.label} setting={props.showSetting ? props.setting : undefined} info={info} />
      <Segmented value={value} options={props.options} onChange={(v) => set(props.setting, v)} />
      {line && <p className="hint">{line}</p>}
    </div>
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
    <span className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${CHIPS[props.kind]}`}>
      {Icon && <Icon size={12} className="shrink-0" />}
      <span className="min-w-0 truncate">{props.children}</span>
    </span>
  )
}

export function Stat(props: { label: string; value: ReactNode; accent?: boolean }): JSX.Element {
  return (
    <div className="min-w-0 rounded-lg border border-ink-700 bg-ink-900/70 px-4 py-3">
      <div className="truncate text-[11px] font-semibold uppercase tracking-wider text-ink-400">{props.label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${props.accent ? 'text-delta-light' : 'text-slate-100'}`}>{props.value}</div>
    </div>
  )
}

export function Segmented<T extends string>(props: { value: T; options: { value: T; label: string; icon?: LucideIcon }[]; onChange: (v: T) => void }): JSX.Element {
  return (
    <div className="inline-flex max-w-full flex-wrap rounded-lg border border-ink-600 bg-ink-900 p-1">
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
