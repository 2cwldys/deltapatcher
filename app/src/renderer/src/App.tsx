import { useEffect } from 'react'
import Sidebar from './components/Sidebar'
import ActivityBar from './components/ActivityBar'
import Modals from './components/Modals'
import { ExportDialog, ImportDialog } from './components/ProjectFileDialogs'
import UpdatePage, { runUpdate } from './pages/UpdatePage'
import ReleasePage, { publishRelease } from './pages/ReleasePage'
import SettingsPage from './pages/SettingsPage'
import ProjectsPage from './pages/ProjectsPage'
import { autoBackup, autoCheck, Page, useApp } from './store/app'
import { addFile } from './store/projectActions'
import { scheduler, TICK } from './store/schedule'

const TITLES: Record<Page, [string, string]> = {
  projects: ['Projects', 'Every game, mod or server DeltaPatcher keeps up to date or releases: check them, update them all at once, or open one.'],
  update: ['Update', 'Bring a folder up to the latest release, downloading only what changed.'],
  release: ['Release', 'Send players a new version.\nOnly what changed is sent, and players download only that.'],
  settings: ['Settings', "This project's settings file, DeltaPatcher's own, the log, and about."]
}
// the pages about one project, whose header names it
const ONE_PROJECT: Page[] = ['update', 'release', 'settings']

// a file double-clicked, given on the command line or dropped onto the window: a project file is imported,
// a settings file listed
window.api.onOpenFile((path) => {
  void addFile(path)
})
// a notification clicked: the page it is about
window.api.onShowPage((page) => {
  if (page in TITLES) useApp.getState().setPage(page as Page)
})

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
  const projectTitle = useApp((s) => (s.projects.length > 1 ? s.projects.find((p) => p.file === s.project)?.title ?? '' : ''))

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined
    load().then(async () => {
      const info = await window.api.info()
      if (info.capture) {
        captureTour()
        return
      }
      if (useApp.getState().settings.check_updates !== false) useApp.getState().checkAppUpdate()
      // every project is checked at the start, then every check_every_hours hours while DeltaPatcher runs; a
      // project with backup_every_hours is backed up as often
      const tick = scheduler(autoCheck)
      const check = (): void => {
        tick(Date.now(), useApp.getState().effective('check_every_hours'))
        void autoBackup()
      }
      check()
      timer = setInterval(check, TICK)
      const opened = await window.api.takeOpenedFile()
      if (opened) await addFile(opened)
    })
    return () => {
      if (timer) clearInterval(timer)
    }
  }, [load])

  // a project file or a settings file dropped onto the window is added as Add a project file adds it
  useEffect(() => {
    const over = (e: DragEvent): void => {
      if (e.dataTransfer?.types.includes('Files')) {
        e.preventDefault()
        e.dataTransfer.dropEffect = 'copy'
      }
    }
    const drop = (e: DragEvent): void => {
      const file = e.dataTransfer?.files?.[0]
      if (!file) return
      e.preventDefault()
      const path = window.api.pathOf(file)
      if (path) void addFile(path)
    }
    window.addEventListener('dragover', over)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragover', over)
      window.removeEventListener('drop', drop)
    }
  }, [])

  const [name, subtitle] = TITLES[page]
  // ASCII only: the page's name, then the project it is about
  const title = ONE_PROJECT.includes(page) && projectTitle ? `${name} - ${projectTitle}` : name
  return (
    <div className="flex h-full bg-ink-950 bg-[radial-gradient(ellipse_at_top_right,rgba(25,179,160,0.10),transparent_55%)]">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="px-8 pb-4 pt-7">
          <h1 className="truncate text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-1 whitespace-pre-line text-sm text-ink-300">{subtitle}</p>
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
      <ImportDialog />
      <ExportDialog />
      <Modals />
    </div>
  )
}
