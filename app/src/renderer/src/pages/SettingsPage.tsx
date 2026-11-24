import { useEffect, useState } from 'react'
import { FileText, FolderOpen, Info, SlidersHorizontal } from 'lucide-react'
import { useApp, Value } from '../store/app'
import { Button, Card, Chip, Field, SettingToggle } from '../components/ui'
import type { AppInfo } from '@shared/types'

const SECTIONS: Record<string, string> = {
  update: 'Update',
  publish: 'Publish',
  server: 'Server',
  window: 'Window'
}

export default function SettingsPage(): JSX.Element {
  const { schema, settingsPath, logDir } = useApp()
  const [info, setInfo] = useState<AppInfo | null>(null)
  useEffect(() => {
    window.api.info().then(setInfo)
  }, [])

  const sections = Array.from(new Set(schema.map((s) => s.section)))
  return (
    <div className="space-y-5">
      <Card title="Files" icon={FileText}>
        <p className="text-sm text-ink-300">
          Every setting lives in <span className="font-mono text-violet-300">deltapatcher.ini</span>, a text file with a line explaining each one.
          <br />
          Edits here are saved there as you type; edits there show here next time.
        </p>
        <div className="mt-4 space-y-2 font-mono text-xs">
          <div className="selectable break-all text-ink-300"><span className="text-ink-400">settings </span>{settingsPath}</div>
          <div className="selectable break-all text-ink-300"><span className="text-ink-400">logs     </span>{logDir}</div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button icon={FileText} onClick={() => window.api.open(settingsPath)}>Open settings file</Button>
          <Button icon={FolderOpen} onClick={() => window.api.open(logDir)}>Open log folder</Button>
        </div>
      </Card>

      {sections.map((sec) => (
        <Card key={sec} title={SECTIONS[sec] ?? sec} icon={SlidersHorizontal}>
          <div className="grid gap-5 md:grid-cols-2">
            {schema.filter((s) => s.section === sec).map((s) =>
              typeof s.default === 'boolean' ? (
                <SettingToggle key={s.key} setting={s.key} label={s.key} hint={s.what} />
              ) : (
                <Field key={s.key} label={s.key} setting={s.key} hint={s.what} mono type={typeof (s.default as Value) === 'number' ? 'number' : 'text'} />
              )
            )}
          </div>
        </Card>
      ))}

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
