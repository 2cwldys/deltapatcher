import { useEffect, useState } from 'react'
import { FileText, FolderKanban, FolderOpen, Info, RotateCcw, Settings2, SlidersHorizontal, Trash2, TriangleAlert } from 'lucide-react'
import { running, SchemaItem, useApp, Value } from '../store/app'
import { removeProject, resetAll, sentences } from '../store/projectActions'
import { folderNameProblem } from '../store/folders'
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
// kept by the Projects page and the project picker, not typed here: the name, the added files, the one shown
const HIDDEN = ['title', 'project_files', 'current_project']

// blocked: a switch that cannot work in this copy, and why (start_with_system, run from source)
function Setting(props: { s: SchemaItem; blocked?: string }): JSX.Element {
  const s = props.s
  return typeof s.default === 'boolean' ? (
    <SettingToggle setting={s.key} label={s.key} hint={sentences(s.what)} blocked={props.blocked} />
  ) : (
    <Field label={s.key} setting={s.key} hint={sentences(s.what)} mono type={typeof (s.default as Value) === 'number' ? 'number' : 'text'}
      check={s.key === 'install_folder' ? folderNameProblem : undefined} />
  )
}

function Sections(props: { names: string[]; schema: SchemaItem[]; blocked?: Record<string, string> }): JSX.Element {
  return (
    <>
      {props.names.map((sec) => (
        <Card key={sec} title={SECTIONS[sec] ?? sec} icon={SlidersHorizontal}>
          <div className="grid gap-5 md:grid-cols-2">
            {props.schema
              .filter((s) => s.section === sec && !HIDDEN.includes(s.key))
              .map((s) => <Setting key={s.key} s={s} blocked={props.blocked?.[s.key]} />)}
          </div>
        </Card>
      ))}
    </>
  )
}

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
      <Card title={`This project: ${project?.title ?? 'Default'}`} icon={FolderKanban}>
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

      <Card title="DeltaPatcher" icon={Settings2}>
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

      <Card title="About" icon={Info}>
        <div className="flex flex-wrap gap-2">
          <Chip kind="info">DeltaPatcher {info?.version}</Chip>
          <Chip kind="muted">{info?.platform}</Chip>
          <Chip kind="muted">MIT licence</Chip>
        </div>
        <p className="selectable mt-3 break-all font-mono text-xs text-ink-400">engine: {info?.engine}</p>
      </Card>

      <div className="pt-6">
        <section className="rounded-xl border border-rose-500/30 bg-rose-500/5 shadow-lg shadow-black/20">
          <header className="flex items-center gap-2.5 border-b border-rose-500/20 px-5 py-3.5 text-sm font-semibold text-slate-100">
            <TriangleAlert size={16} className="text-rose-300" />
            Reset DeltaPatcher
          </header>
          <div className="p-5">
            <p className="whitespace-pre-line text-sm text-ink-300">
              {'DeltaPatcher as it was just installed: every project but Default leaves, and the settings go back to their defaults.\n' +
                'The folders projects update or release are never touched, nor the signing key or SSH keys.\n' +
                'Trusted server host keys, the log and prepared releases stay.\n' +
                'A copy of the settings files is kept beside them, so a reset can be undone by hand.'}
            </p>
            <div className="mt-4">
              <Button variant="danger" icon={RotateCcw} disabled={working} onClick={resetAll}>Reset DeltaPatcher...</Button>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
