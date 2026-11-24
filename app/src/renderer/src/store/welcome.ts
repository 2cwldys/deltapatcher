// The welcome screen: shown at the start while no project is set up yet (none has a folder to keep up to date or
// a folder to release), until it is closed; "Getting started" on the Projects page opens it again. Closed is
// kept in the window's own storage, which Reset clears. Nothing here needs the window, so a test can run it.
import type { KeyStore } from './folds'

const KEY = 'deltapatcher.welcome'

// a project that is set up: it has a folder to keep up to date, or a folder to release
export function isSetUp(p: { install_dir: string; publish_src: string; missing?: boolean }): boolean {
  return !p.missing && Boolean(p.install_dir || p.publish_src)
}

// shown at the start: nothing is set up, and it was not closed before
export function welcomeAtStart(projects: { install_dir: string; publish_src: string; missing?: boolean }[], storage: KeyStore | null): boolean {
  return !projects.some(isSetUp) && !welcomeClosed(storage)
}

export function welcomeClosed(storage: KeyStore | null): boolean {
  try {
    return storage?.getItem(KEY) === 'closed'
  } catch {
    return false
  }
}

export function closeWelcome(storage: KeyStore | null): void {
  try {
    storage?.setItem(KEY, 'closed')
  } catch {
    // not kept: it shows again at the next start while nothing is set up
  }
}
