// Dialogs as promises: confirm, a passphrase (once or twice), a server's host keys, a name.
import { create } from 'zustand'

export interface HostKey {
  type: string
  fingerprint: string
}

export type ModalSpec =
  | { kind: 'confirm'; title: string; body: string; confirm: string; danger?: boolean }
  | { kind: 'passphrase'; title: string; body: string; twice: boolean; minLength: number; allowEmpty?: boolean }
  | { kind: 'hostkeys'; host: string; keys: HostKey[] }
  | { kind: 'info'; title: string; body: string; code?: string }
  // one line of text; `check` says what is wrong with it ('' when nothing)
  | { kind: 'prompt'; title: string; body: string; value: string; confirm: string; check?: (v: string) => string }

interface ModalState {
  spec: ModalSpec | null
  resolve: ((v: unknown) => void) | null
  // Force stop: no dialog opens, each answers as cancelled at once
  blocked: boolean
  open: (spec: ModalSpec) => Promise<unknown>
  close: (value: unknown) => void
  // on: the dialog that is open closes as cancelled, and none opens until it is called with off
  block: (on: boolean) => void
}

// what a dialog answers when it is closed without an answer
function cancelled(spec: ModalSpec): unknown {
  return spec.kind === 'passphrase' || spec.kind === 'prompt' ? null : spec.kind === 'info' ? undefined : false
}

export const useModal = create<ModalState>((set, get) => ({
  spec: null,
  resolve: null,
  blocked: false,
  // a new dialog closes one that is still open as cancelled, so no caller waits forever
  open: (spec) =>
    new Promise((resolve) => {
      if (get().blocked) {
        resolve(cancelled(spec))
        return
      }
      const prev = get().resolve
      const prevSpec = get().spec
      if (prev && prevSpec) prev(cancelled(prevSpec))
      set({ spec, resolve })
    }),
  close: (value) => {
    const r = get().resolve
    set({ spec: null, resolve: null })
    if (r) r(value)
  },
  block: (on) => {
    const r = get().resolve
    const spec = get().spec
    if (!on) {
      set({ blocked: false })
      return
    }
    set({ blocked: true, spec: null, resolve: null })
    if (r && spec) r(cancelled(spec))
  }
}))

export const ask = {
  confirm: (title: string, body: string, confirm = 'OK', danger = false): Promise<boolean> =>
    useModal.getState().open({ kind: 'confirm', title, body, confirm, danger }) as Promise<boolean>,
  passphrase: (title: string, body: string, twice = false, minLength = 0, allowEmpty = false): Promise<string | null> =>
    useModal.getState().open({ kind: 'passphrase', title, body, twice, minLength, allowEmpty }) as Promise<string | null>,
  hostKeys: (host: string, keys: HostKey[]): Promise<boolean> =>
    useModal.getState().open({ kind: 'hostkeys', host, keys }) as Promise<boolean>,
  info: (title: string, body: string, code?: string): Promise<void> =>
    useModal.getState().open({ kind: 'info', title, body, code }) as Promise<void>,
  prompt: (title: string, body: string, value = '', confirm = 'OK', check?: (v: string) => string): Promise<string | null> =>
    useModal.getState().open({ kind: 'prompt', title, body, value, confirm, check }) as Promise<string | null>
}
