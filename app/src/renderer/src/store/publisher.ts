// What the window shows of a project's publisher: the change a publisher made to a project that waits for the
// person (Apply, Not now), the files a release adds, changes and removes when its publisher shows them, its whole
// file list, the release notes, and the words of the import and export dialogs about update capable files.
// Nothing here uses the window, so a test can run it as it is.

// one detail a publisher changed: from what the project has to what the publisher gives
export interface ChangeItem {
  field: string
  label: string
  from: string
  to: string
}

// the engine's publisher_change: what changed, its hash (what Not now and Apply name), whether it moves the
// project to another address or key and how that is signed, and the fields Apply may take
export interface PublisherChange {
  changes: ChangeItem[]
  words: string
  hash: string
  moves: string[]
  trust: '' | 'signed' | 'unsigned' | 'refused'
  warning: string
  signer: string
  trusted: string
  applicable: string[]
  project?: string
  file?: string
}

export interface FileChanges {
  new: string[]
  changed: string[]
  removed: string[]
  kept: string[]
}

export interface ReleaseFile {
  path: string
  size: number
}

// the parts of a check's or an update's result read here
export interface ReleaseView {
  show_files?: boolean
  changes?: FileChanges
  files?: ReleaseFile[]
  notes?: string
  publisher_change?: PublisherChange
  release?: { name: string; version: string; published: string }
}

export const NOTES_MAX = 4000

// the line a project's row and the Update page show
export function promptLine(c: PublisherChange): string {
  return `The publisher changed this project: ${c.words}.`
}

// a detail as a change shows it
export function shown(value: string): string {
  return value || '(none)'
}

// each change, from what to what, one a line
export function changeLines(c: PublisherChange): string[] {
  return c.changes.map((i) => `${i.label}: ${shown(i.from)} -> ${shown(i.to)}`)
}

// the question Apply asks: what changes, the warning a new address or key comes with, and what stays; red when
// the project trusts no publisher key and it moves the project
export function applyQuestion(title: string, c: PublisherChange): { title: string; body: string; danger: boolean } {
  const refused = c.changes.filter((i) => !c.applicable.includes(i.field)).map((i) => i.label)
  const parts = [changeLines(c).join('\n')]
  if (c.warning) parts.push(c.warning)
  if (refused.length) parts.push(`Not applied: ${refused.join(', ')}.`)
  parts.push('Its folder, its name here and your own settings stay as they are.')
  return { title: `Apply the publisher's change to ${title}?`, body: parts.join('\n\n'), danger: c.trust === 'unsigned' && c.moves.length > 0 }
}

// the engine run of Apply (or Not now): only the change that was shown, by its hash
export function acceptArgs(file: string, c: PublisherChange, notNow: boolean): string[] {
  return ['project', 'accept', file, notNow ? '--not-now' : '--yes', `--expect=${c.hash}`]
}

// a result once Apply or Not now answered its prompt: the prompt gone, the rest as it was
export function withoutPrompt<T extends ReleaseView>(r: T): T {
  if (!r || !r.publisher_change) return r
  const rest = { ...r }
  delete rest.publisher_change
  return rest
}

// The window once a project's prompt is answered (Apply, Not now): its row's last result without it, and the
// Update page's too while that project is the one shown; everything else as it was
export function settled<Run extends { result?: ReleaseView }>(
  runs: Record<string, Run>, results: Record<string, unknown>, shownFile: string, file: string
): { runs: Record<string, Run>; results: Record<string, unknown> } {
  const run = runs[file]
  const nextRuns = run?.result?.publisher_change ? { ...runs, [file]: { ...run, result: withoutPrompt(run.result) } as Run } : runs
  const update = results.update as ReleaseView | undefined
  const nextResults = shownFile === file && update?.publisher_change ? { ...results, update: withoutPrompt(update) } : results
  return { runs: nextRuns, results: nextResults }
}

// the prompt a project shows: what its last check or update found, while nothing runs on it; none otherwise
export function promptOf(run: { state: string; result?: ReleaseView } | undefined): PublisherChange | undefined {
  return run?.state === 'done' ? run.result?.publisher_change : undefined
}

// "What changed" has something to show: the files by name, the whole list, or release notes
export function hasWhatChanged(r: ReleaseView | undefined | null): boolean {
  return Boolean(r && (r.changes || r.files?.length || r.notes))
}

// a list of the "What changed" dialog, as its search box narrows it: the names shown (at most `most`), how many
// match, and how many there are
export interface ListView {
  key: keyof FileChanges
  label: string
  names: string[]
  matched: number
  total: number
}

const LISTS: [keyof FileChanges, string][] = [['new', 'New'], ['changed', 'Changed'], ['removed', 'Removed'], ['kept', 'Kept: your own copies']]

function matches(search: string): (name: string) => boolean {
  const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean)
  return (name) => words.every((w) => name.toLowerCase().includes(w))
}

// the lists of files a release adds, changes, removes and keeps; [] when its publisher shows no file names
export function changeLists(r: ReleaseView | undefined | null, search = '', most = 300): ListView[] {
  if (!r?.changes) return []
  const keep = matches(search)
  return LISTS.map(([key, label]) => {
    const all = r.changes?.[key] ?? []
    const found = all.filter(keep)
    return { key, label, names: found.slice(0, most), matched: found.length, total: all.length }
  })
}

// the Update page's lines of names under the counts: each list that has any, its first `most` names and how many
// more; [] when the publisher shows no file names
export function nameLines(r: ReleaseView | undefined | null, most = 5): string[] {
  return changeLists(r).filter((l) => l.total > 0).map((l) =>
    `${l.label}: ${l.names.slice(0, most).join(', ')}${l.total > most ? ` and ${l.total - most} more` : ''}`)
}

// the release's whole list, as the search box narrows it; null when the result has none
export function fileList(r: ReleaseView | undefined | null, search = '', most = 300): { files: ReleaseFile[]; matched: number; total: number } | null {
  if (!r?.files) return null
  const keep = matches(search)
  const found = r.files.filter((f) => keep(f.path))
  return { files: found.slice(0, most), matched: found.length, total: r.files.length }
}

// what is wrong with release notes as they are typed ('' when nothing)
export function notesProblem(text: string): string {
  return text.length > NOTES_MAX ? `At most ${NOTES_MAX} characters: this is ${text.length}.` : ''
}

// a project of a project file in the import dialog: the project here it updates, what that changes, and
// whether the person chose to add it as a new project instead
export interface ImportRow {
  title: string
  pick: boolean
  asNew?: boolean
  updates?: { project: string; file: string }
  change?: PublisherChange | null
  older?: boolean
}

// it updates a project here (not added as a new one)
export function inPlace(e: ImportRow): boolean {
  return Boolean(e.updates) && !e.asNew
}

// the line under such a project: what it updates and what changes
export function updateLine(e: ImportRow): string {
  const name = e.updates?.project ?? e.title
  if (e.older) return `This updates ${name}.\nIt has details newer than this file, so nothing changes.`
  if (!e.change) return `This updates ${name}.\nNothing in it changes.`
  return `This updates ${name}: ${e.change.words}.`
}

// the import dialog's button: Add, Update, or both
export function importButton(rows: ImportRow[], working = false): string {
  if (working) return 'Adding...'
  const picked = rows.filter((e) => e.pick)
  const upd = picked.filter(inPlace).length
  const add = picked.length - upd
  const projects = (n: number): string => (n === 1 ? '1 project' : `${n} projects`)
  if (upd && add) return `Add ${add}, update ${upd}`
  if (upd) return `Update ${projects(upd)}`
  return `Add ${projects(add)}`
}

// the export dialog's choice at first: update capable when every project in it shares its updates
export function updatesDefault(projects: { share_updates?: boolean }[]): boolean {
  return projects.length > 0 && projects.every((p) => p.share_updates !== false)
}

// the hover text of the update capable mark in the import dialog
export const UPDATE_CAPABLE = 'Update capable: the publisher can change this project later; you are asked before anything changes.'
