// The prompt a project shows when its publisher changed it (on its row on the Projects page, and on the Update
// page): what changed, in one line with the whole of it behind the (i), and Apply / Not now. Nothing changes until
// the person answers; Apply asks once more, with the warning a new address or key comes with.
import { BellRing, Check, Clock } from 'lucide-react'
import { useApp } from '../store/app'
import { applyPublisher, notNowPublisher } from '../store/projectActions'
import { changeLines, promptLine, PublisherChange } from '../store/publisher'
import { Button, InfoTip } from './ui'

export default function PublisherPrompt(props: { file: string; change: PublisherChange }): JSX.Element {
  const busy = useApp((s) => Boolean(s.busy))
  const { file, change } = props
  const more = [...changeLines(change), ...(change.warning ? ['', change.warning] : [])].join('\n')
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border px-4 py-2.5 text-sm ${
      change.moves.length ? 'border-blob/30 bg-blob/10 text-blob-light' : 'border-sky-500/30 bg-sky-500/10 text-sky-200'}`}>
      <BellRing size={15} className="shrink-0" />
      <span className="min-w-0 flex-1">{promptLine(change)}</span>
      <InfoTip text={more} />
      <div className="flex shrink-0 gap-2">
        <Button small variant="primary" icon={Check} disabled={busy} onClick={() => void applyPublisher(file, change)}>Apply</Button>
        <Button small icon={Clock} disabled={busy} title="Asked again once the publisher changes it again"
          onClick={() => void notNowPublisher(file, change)}>Not now</Button>
      </div>
    </div>
  )
}
