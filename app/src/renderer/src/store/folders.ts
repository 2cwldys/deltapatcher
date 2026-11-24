// The rules of a project's name, which the name of the folder a project goes into (install_folder) follows
// too, and the folder the import dialog suggests for such a project. Nothing here uses the window, so a test
// can run it as it is.

const RESERVED = new Set(['CON', 'PRN', 'AUX', 'NUL', ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((i) => [`COM${i}`, `LPT${i}`])])

// what is wrong with a name by the engine's rules ('' when nothing), said while it is typed
export function nameRules(name: string): string {
  if (name.length > 60) return 'At most 60 characters.'
  if (!/^[A-Za-z0-9 _.()-]+$/.test(name)) return 'Letters, digits, spaces and - _ . ( ) only.'
  if (name.startsWith('-')) return 'It cannot start with a dash.'
  if (name.endsWith('.')) return 'It cannot end with a dot.'
  if (RESERVED.has(name.split('.')[0].trim().toUpperCase())) return 'Windows keeps that name for a device: choose another.'
  return ''
}

// what is wrong with install_folder as typed ('' when nothing; empty is fine: the folder a player picks)
export function folderNameProblem(name: string): string {
  const n = name.trim()
  return n ? nameRules(n) : ''
}

// The folder field after a folder is picked for a project whose file names a folder for it: that folder
// inside the one picked, unless the one picked already is it (letter case aside on Windows).
export function suggestedFolder(picked: string, folder: string): string {
  if (!folder) return picked
  const windows = picked.includes('\\')
  const trimmed = picked.replace(/[\\/]+$/, '')
  const last = trimmed.split(/[\\/]/).pop() ?? ''
  if (windows ? last.toLowerCase() === folder.toLowerCase() : last === folder) return trimmed
  return `${trimmed}${windows ? '\\' : '/'}${folder}`
}
