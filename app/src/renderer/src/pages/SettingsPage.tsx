import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, FileText, FolderKanban, FolderOpen, Info, RotateCcw, Settings2, SlidersHorizontal, Trash2, TriangleAlert } from 'lucide-react'
import { running, SchemaItem, useApp, Value } from '../store/app'
import { removeProject, resetAll, sentences } from '../store/projectActions'
import { folderNameProblem } from '../store/folders'
import { sizeProblem } from '../store/backups'
import { useFold } from '../store/folds'
import { HIDDEN, splitMore, wordsOf } from '../store/settingWords'
import BackupsSection from '../components/BackupsSection'
import { Button, Card, Chip, Field, SettingToggle } from '../components/ui'
import type { AppInfo } from '@shared/types'

const SECTIONS: Record<string, string> = {
  project: 'Project',
  update: 'Update',
  publish: 'Publish',
  server: 'Server',
  window: 'Window',
  general: 'Projects',
  backup: 'Backups'
}
// DeltaPatcher's own settings, in the main file whichever project is shown
const APP_SECTIONS = ['window', 'general']

// A setting in plain words (store/settingWords.ts): its label, its own name small beside it, one short line
// under it, and the engine's whole explanation behind the (i). blocked: a switch that cannot work in this copy,
// and why (start_with_system, run from source).
function Setting(props: { s: SchemaItem; blocked?: string }): JSX.Element {
  const s = props.s
  const words = wordsOf(s.key)
  return typeof s.default === 'boolean' ? (
    <SettingToggle setting={s.key} label={words.label} showSetting hint={sentences(s.what)} line={words.line} blocked={props.blocked} />
  ) : (
    <Field label={words.label} setting={s.key} showSetting hint={sentences(s.what)} line={words.line} mono
      type={typeof (s.default as Value) === 'number' ? 'number' : 'text'}
      browse={FOLDERS.includes(s.key) ? 'folder' : undefined}
      check={s.key === 'install_folder' ? folderNameProblem : s.key === 'backup_max_size' ? sizeProblem : undefined} />
  )
}

// a folder chosen with Browse here: these settings are typed on no other page
const FOLDERS = ['backup_dir']

// A section's settings: the ones often changed, then More, which unfolds the ones rarely changed (each in the
// order of the settings file; the window remembers it as it remembers its cards)
function SectionFields(props: { sec: string; items: SchemaItem[]; blocked?: Record<string, string> }): JSX.Element {
  const { shown, more } = splitMore(props.items.map((s) => s.key))
  const byKey = (k: string): SchemaItem => props.items.find((s) => s.key === k) as SchemaItem
  const [folded, setFolded] = useFold(`settings/${props.sec}/more`, true)
  const grid = (keys: string[]): JSX.Element => (
    <div className="grid gap-5 md:grid-cols-2">
      {keys.map((k) => <Setting key={k} s={byKey(k)} blocked={props.blocked?.[k]} />)}
    </div>
  )
  return (
    <>
      {grid(shown)}
      {more.length > 0 && (
        <div className={shown.length ? 'mt-5 border-t border-ink-700/70 pt-4' : ''}>
          <button type="button" aria-expanded={!folded} onClick={() => setFolded(!folded)}
            className="flex items-center gap-1.5 rounded text-sm font-medium text-ink-300 transition-colors hover:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-delta/40">
            {folded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
            More
            <span className="text-xs font-normal text-ink-400">{`${more.length} rarely changed`}</span>
          </button>
          {!folded && <div className="mt-4">{grid(more)}</div>}
        </div>
      )}
    </>
  )
}

// each section's settings in a card; the Backups section's card also holds the backups themselves
function Sections(props: { names: string[]; schema: SchemaItem[]; blocked?: Record<string, string> }): JSX.Element {
  return (
    <>
      {props.names.map((sec) => {
        const items = props.schema.filter((s) => s.section === sec && !HIDDEN.includes(s.key))
        const fields = <SectionFields sec={sec} items={items} blocked={props.blocked} />
        return sec === 'backup' ? (
          <BackupsSection key={sec}>{fields}</BackupsSection>
        ) : (
          <Card key={sec} fold={`settings/${sec}`} title={SECTIONS[sec] ?? sec} icon={SlidersHorizontal}>
            {fields}
          </Card>
        )
      })}
    </>
  )
}

const RESET =
  'DeltaPatcher as it was just installed: every project but Default leaves, and the settings go back to their defaults.\n' +
  'The folders projects update or release are never touched, nor the signing key or SSH keys.\n' +
  'Trusted server host keys, the log and prepared releases stay.\n' +
  'A copy of the settings files is kept beside them, so a reset can be undone by hand.'

export default function SettingsPage(): JSX.Element {
  const { schema, settingsPath, mainPath, logDir } = useApp()
  const project = useApp((s) => s.projects.find((p) => p.file === s.project))
  const working = useApp((s) => Boolean(s.busy) || Boolean(s.allJob) || Object.values(s.runs).some((r) => running(r)))
  const [info, setInfo] = useState<AppInfo | null>(null)
  useEffect(() => {
    window.api.info().then(setInfo)
  }, [])

  const sections = Array.from(new Set(schema.map((s) => s.section))).filter((s) => schema.some((x) => x.section === s && !HIDDEN.includes(x.key)))
  const own = sections.filter((s) => !APP_SECTIONS.includes(s))
  const app = sections.filter((s) => APP_SECTIONS.includes(s))
  return (
    <div className="space-y-5">
      <Card fold="settings/this-project" title={`This project: ${project?.title ?? 'Default'}`} icon={FolderKanban}>
        <p className="text-sm text-ink-300">
          What this project updates and releases lives in its own settings file, a text file with a line explaining each setting.
          <br />
          Edits here are saved there as you type; edits there show here next time.
        </p>
        <div className="mt-4 space-y-2 font-mono text-xs">
          <div className="selectable break-all text-ink-300"><span className="text-ink-400">settings </span>{settingsPath}</div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button icon={FileText} onClick={() => window.api.open(settingsPath)}>Open settings file</Button>
          {project && project.kind !== 'main' && (
            <Button variant="remove" icon={Trash2} disabled={working} onClick={() => removeProject(project)}>Remove this project...</Button>
          )}
        </div>
      </Card>

      <Sections names={own} schema={schema} />

      <Card fold="settings/deltapatcher" title="DeltaPatcher" icon={Settings2}>
        <p className="text-sm text-ink-300">
          DeltaPatcher's own settings are the same for every project.
          <br />
          They live in <span className="font-mono text-violet-300">deltapatcher.ini</span>, with the Default project's.
        </p>
        <div className="mt-4 space-y-2 font-mono text-xs">
          <div className="selectable break-all text-ink-300"><span className="text-ink-400">settings </span>{mainPath}</div>
          <div className="selectable break-all text-ink-300"><span className="text-ink-400">logs     </span>{logDir}</div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button icon={FileText} onClick={() => window.api.open(mainPath)}>Open settings file</Button>
          <Button icon={FolderOpen} onClick={() => window.api.open(logDir)}>Open log folder</Button>
        </div>
      </Card>

      <Sections names={app} schema={schema} blocked={{ start_with_system: info?.startWithSystem ?? '' }} />

      <Card fold="settings/about" title="About" icon={Info}>
        <div className="flex flex-wrap gap-2">
          <Chip kind="info">DeltaPatcher {info?.version}</Chip>
          <Chip kind="muted">{info?.platform}</Chip>
          <Chip kind="muted">MIT licence</Chip>
        </div>
        <p className="selectable mt-3 break-all font-mono text-xs text-ink-400">engine: {info?.engine}</p>
      </Card>

      <div className="pt-6">
        <Card fold="settings/reset" title="Reset DeltaPatcher" icon={TriangleAlert} tone="danger" info={RESET}>
          <p className="text-sm text-ink-300">{RESET.split('\n')[0]}</p>
          <div className="mt-4">
            <Button variant="danger" icon={RotateCcw} disabled={working} onClick={resetAll}>Reset DeltaPatcher...</Button>
          </div>
        </Card>
      </div>
    </div>
  )
}
