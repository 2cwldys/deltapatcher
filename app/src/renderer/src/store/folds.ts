// Which boxes are folded: every card on every page folds with the arrow at the right of its header, and the
// window remembers it in its own storage (localStorage), by page and card, so a card folded once stays folded.
// A card is open until it is folded, unless it says otherwise (the server setup, once it is all done). Reset
// clears the window's storage, and every card opens again.
import { create } from 'zustand'

export interface KeyStore {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
}

const KEY = 'deltapatcher.folded'

// the window's storage; null where there is none (a test) or it cannot be used
export function windowStorage(): KeyStore | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

// the cards folded or opened by hand, by their id ('page/card'): true folded, false open. Anything else in the
// storage is passed over.
export function readFolds(storage: KeyStore | null): Record<string, boolean> {
  try {
    const v: unknown = JSON.parse(storage?.getItem(KEY) ?? '{}')
    if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
    return Object.fromEntries(Object.entries(v as Record<string, unknown>).filter((e): e is [string, boolean] => typeof e[1] === 'boolean'))
  } catch {
    return {}
  }
}

// whether a card is folded: as it was left by hand, else as it starts (open, unless it says otherwise)
export function isFolded(folds: Record<string, boolean>, id: string, startsFolded = false): boolean {
  return Object.prototype.hasOwnProperty.call(folds, id) ? folds[id] : startsFolded
}

export function withFold(folds: Record<string, boolean>, id: string, folded: boolean): Record<string, boolean> {
  return { ...folds, [id]: folded }
}

interface FoldState {
  folds: Record<string, boolean>
  setFolded: (id: string, folded: boolean) => void
}

export function foldStore(storage: KeyStore | null) {
  return create<FoldState>((set, get) => ({
    folds: readFolds(storage),
    setFolded: (id, folded) => {
      const next = withFold(get().folds, id, folded)
      set({ folds: next })
      try {
        storage?.setItem(KEY, JSON.stringify(next))
      } catch {
        // not kept for the next start: the card still folds now
      }
    }
  }))
}

export const useFolds = foldStore(windowStorage())

// a card's fold, for its component: whether it is folded now, and the switch
export function useFold(id: string, startsFolded = false): [boolean, (folded: boolean) => void] {
  const folded = useFolds((s) => isFolded(s.folds, id, startsFolded))
  const setFolded = useFolds((s) => s.setFolded)
  return [folded, (f: boolean) => setFolded(id, f)]
}
