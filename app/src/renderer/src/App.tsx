import { useEffect } from 'react'
import Sidebar from './components/Sidebar'
import ActivityBar from './components/ActivityBar'
import Modals from './components/Modals'
import UpdatePage, { runUpdate } from './pages/UpdatePage'
import ReleasePage, { publishRelease } from './pages/ReleasePage'
import SettingsPage from './pages/SettingsPage'
import ProjectsPage from './pages/ProjectsPage'
import { Page, useApp } from './store/app'

const TITLES: Record<Page, [string, string]> = {
  projects: ['Projects', 'Every game, mod or server DeltaPatcher keeps up to date or releases: check them, update them all at once, or open one.'],
  update: ['Update', 'Bring a folder up to the latest release, downloading only what changed.'],
  release: ['Release', 'Send players a new version. Only what changed is sent, and players download only that.'],
  settings: ['Settings', "This project's settings file, DeltaPatcher's own, the log, and about."]
}

// README screenshots (app --capture=DIR): publish and check a demo release, check every demo project, then
// show each page
async function captureTour(): Promise<void> {
  const pages: Page[] = ['projects', 'update', 'release', 'settings']
  const st = useApp.getState()
  // serve the demo store, publish its new version, and check the demo game against it
  st.startServe()
  for (let waited = 0; !useApp.getState().serving?.url; waited += 100) {
    if (waited > 20000) {
      window.api.capture.done()      // the port is taken or the store is missing: give up, do not hang
      return
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  await publishRelease()
  useApp.setState({ autoChecked: true })
  await st.runAll(true)
  await runUpdate(true)
  let i = 0
  const next = (): void => {
    if (i >= pages.length) {
      useApp.getState().stopServe()
      window.api.capture.done()
      return
    }
    const p = pages[i++]
    st.setPage(p)
    useApp.setState({ consoleOpen: false })
    setTimeout(() => window.api.capture.ready(p), 900)
  }
  window.api.capture.onNext(next)
  next()
}

export default function App(): JSX.Element {
  const page = useApp((s) => s.page)
  const loaded = useApp((s) => s.loaded)
  const load = useApp((s) => s.load)

  useEffect(() => {
    load().then(async () => {
      const info = await window.api.info()
      if (info.capture) captureTour()
      else if (useApp.getState().settings.check_updates !== false) useApp.getState().checkAppUpdate()
    })
  }, [load])

  const [title, subtitle] = TITLES[page]
  return (
    <div className="flex h-full bg-ink-950 bg-[radial-gradient(ellipse_at_top_right,rgba(25,179,160,0.10),transparent_55%)]">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="px-8 pb-4 pt-7">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-1 text-sm text-ink-300">{subtitle}</p>
        </header>
        {/* keyed by page: each page opens at its top, not where the last one was scrolled to */}
        <div key={page} className="min-h-0 flex-1 overflow-y-auto px-8 pb-8">
          {!loaded ? (
            <div className="text-sm text-ink-400">Starting the engine...</div>
          ) : page === 'projects' ? (
            <ProjectsPage />
          ) : page === 'update' ? (
            <UpdatePage />
          ) : page === 'release' ? (
            <ReleasePage />
          ) : (
            <SettingsPage />
          )}
        </div>
        <ActivityBar />
      </main>
      <Modals />
    </div>
  )
}
