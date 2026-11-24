import { useEffect, useState } from 'react'
import { FileText, FolderKanban, FolderOpen, Info, Settings2, SlidersHorizontal } from 'lucide-react'
import { SchemaItem, useApp, Value } from '../store/app'
import { Button, Card, Chip, Field, SettingToggle } from '../components/ui'
import type { AppInfo } from '@shared/types'

const SECTIONS: Record<string, string> = {
  update: 'Update',
  publish: 'Publish',
  server: 'Server',
  window: 'Window',
  general: 'Projects'
}
// DeltaPatcher's own settings, in the main file whichever project is shown
const APP_SECTIONS = ['window', 'general']
// kept by the Projects page and the project picker, not typed here: the name, the added files, the one shown
const HIDDEN = ['title', 'project_files', 'current_project']

function Setting(props: { s: SchemaItem }): JSX.Element {
  const s = props.s
  return typeof s.default === 'boolean' ? (
    <SettingToggle setting={s.key} label={s.key} hint={s.what} />
  ) : (
    <Field label={s.key} setting={s.key} hint={s.what} mono type={typeof (s.default as Value) === 'number' ? 'number' : 'text'} />
  )
}

function Sections(props: { names: string[]; schema: SchemaItem[] }): JSX.Element {
  return (
    <>
      {props.names.map((sec) => (
        <Card key={sec} title={SECTIONS[sec] ?? sec} icon={SlidersHorizontal}>
          <div className="grid gap-5 md:grid-cols-2">
            {props.schema.filter((s) => s.section === sec && !HIDDEN.includes(s.key)).map((s) => <Setting key={s.key} s={s} />)}
          </div>
        </Card>
      ))}
    </>
  )
}

export default function SettingsPage(): JSX.Element {
  const { schema, settingsPath, mainPath, logDir } = useApp()
  const title = useApp((s) => s.projects.find((p) => p.file === s.project)?.title ?? 'Default')
  const [info, setInfo] = useState<AppInfo | null>(null)
  useEffect(() => {
    window.api.info().then(setInfo)
  }, [])

  const sections = Array.from(new Set(schema.map((s) => s.section))).filter((s) => schema.some((x) => x.section === s && !HIDDEN.includes(x.key)))
  const own = sections.filter((s) => !APP_SECTIONS.includes(s))
  const app = sections.filter((s) => APP_SECTIONS.includes(s))
  return (
    <div className="space-y-5">
      <Card title={`This project: ${title}`} icon={FolderKanban}>
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
        </div>
      </Card>

      <Sections names={own} schema={schema} />

      <Card title="DeltaPatcher" icon={Settings2}>
        <p className="text-sm text-ink-300">
          DeltaPatcher's own settings are the same for every project. They live in <span className="font-mono text-violet-300">deltapatcher.ini</span>, with the Default project's.
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

      <Sections names={app} schema={schema} />

      <Card title="About" icon={Info}>
        <div className="flex flex-wrap gap-2">
          <Chip kind="info">DeltaPatcher {info?.version}</Chip>
          <Chip kind="muted">{info?.platform}</Chip>
          <Chip kind="muted">MIT licence</Chip>
        </div>
        <p className="selectable mt-3 break-all font-mono text-xs text-ink-400">engine: {info?.engine}</p>
      </Card>
    </div>
  )
}
