// The welcome screen, at the first start (no project set up yet) and from "Getting started" on the Projects
// page: two ways in. Keep a game up to date: add the project file its maker gave, or type the address they gave,
// then pick the game's folder. Publish my own releases: the Release page. It never blocks the pages: a click in
// the sidebar shows that page, and Close puts it away.
import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, FilePlus, Link2, Plus, RefreshCw, Rocket } from 'lucide-react'
import { useApp } from '../store/app'
import { newProject, openProjectFile } from '../store/projectActions'
import { Button, Field } from './ui'

function Choice(props: { icon: typeof Rocket; title: string; text: string; children: React.ReactNode; tone: 'delta' | 'blob' }): JSX.Element {
  const Icon = props.icon
  return (
    <section className="flex min-w-0 flex-col rounded-xl border border-ink-700 bg-ink-850/80 p-6 shadow-lg shadow-black/20">
      <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${props.tone === 'delta' ? 'bg-delta/15 text-delta-light' : 'bg-blob/15 text-blob-light'}`}>
        <Icon size={22} />
      </span>
      <h2 className="mt-4 text-lg font-semibold text-slate-100">{props.title}</h2>
      <p className="mt-1.5 whitespace-pre-line text-sm leading-relaxed text-ink-300">{props.text}</p>
      <div className="mt-auto flex flex-wrap gap-2 pt-5">{props.children}</div>
    </section>
  )
}

export default function Welcome(): JSX.Element {
  const setPage = useApp((s) => s.setPage)
  const title = useApp((s) => s.projects.find((p) => p.file === s.project)?.title ?? 'Default')
  const free = useApp((s) => !s.settings.install_dir && !s.settings.publish_src)
  const ready = useApp((s) => Boolean(String(s.settings.source_url ?? '').trim() && String(s.settings.install_dir ?? '').trim()))
  const count = useApp((s) => s.projects.length)
  const [typing, setTyping] = useState(false)
  // a project file added from here: the Projects page shows what it added
  const adding = useRef<number | null>(null)
  useEffect(() => {
    if (adding.current !== null && count > adding.current) setPage('projects')
  }, [count, setPage])
  const addFile = (): void => {
    adding.current = count
    void openProjectFile()
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-5 md:grid-cols-2">
        <Choice icon={RefreshCw} tone="delta" title="Keep a game up to date"
          text={"Add the project file the game's maker gave you, or type the address they gave you.\nThen pick the game's folder: that is all."}>
          <Button variant="primary" icon={FilePlus} onClick={addFile}>Add a project file...</Button>
          <Button icon={Link2} onClick={() => setTyping(true)}>Type an address</Button>
        </Choice>
        <Choice icon={Rocket} tone="blob" title="Publish my own releases"
          text={'Send players your game, mod or server.\nEach new release sends only what changed.'}>
          <Button variant="primary" icon={ArrowRight} onClick={() => setPage('release')}>Open the Release page</Button>
        </Choice>
      </div>

      {typing && (
        <section className="rounded-xl border border-ink-700 bg-ink-850/80 p-6 shadow-lg shadow-black/20">
          {free ? (
            <>
              <h2 className="text-sm font-semibold text-slate-100">{`The address and the folder, for ${title}`}</h2>
              <div className="mt-4 grid gap-5">
                <Field label="Address" setting="source_url" mono placeholder="https://game.example.com/"
                  hint={"The address the game's maker gave you."} />
                <Field label="Folder" setting="install_dir" browse="folder" mono placeholder="The game folder"
                  hint={'The folder to keep up to date.\nFiles you added yourself are never removed.'} />
              </div>
              <div className="mt-5 flex flex-wrap items-center gap-3">
                <Button variant="primary" icon={Check} disabled={!ready} onClick={() => setPage('update')}>Done</Button>
                <span className="text-xs text-ink-400">{ready ? 'The Update page checks and updates it.' : 'Type the address and pick the folder first.'}</span>
              </div>
            </>
          ) : (
            <>
              <h2 className="text-sm font-semibold text-slate-100">{`${title} is set up already`}</h2>
              <p className="mt-1.5 text-sm text-ink-300">A new project keeps this game apart from it.</p>
              <div className="mt-4">
                <Button icon={Plus} onClick={() => void newProject()}>New project...</Button>
              </div>
            </>
          )}
        </section>
      )}

      <p className="hint">{'Close this screen any time: every page is in the sidebar.\nGetting started, on the Projects page, opens it again.'}</p>
    </div>
  )
}
