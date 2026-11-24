// Runs the engine: deltapatcher-engine --json <command> ..., one JSON object a line on its standard output.
//
// Which engine:
//   1. the one inside the packaged app (resources/engine/deltapatcher-engine[.exe])
//   2. DELTAPATCHER_ENGINE, when set (a path to a deltapatcher-engine or a deltapatcher-cli)
//   3. app/resources/engine/ (put there by scripts/prepare.mjs from dist/engine/)
//   4. otherwise the Python package in this repository: py -3 -m deltapatcher / python3 -m deltapatcher
import { spawn, ChildProcessWithoutNullStreams } from 'child_process'
import { existsSync } from 'fs'
import { dirname, join } from 'path'
import { app } from 'electron'
import { ENGINE_COMMANDS, EngineEvent, EngineOutcome } from '../shared/types'

interface EngineCommand {
  cmd: string
  pre: string[]
  env: NodeJS.ProcessEnv
  cwd?: string
  describe: string
}

const EXE = process.platform === 'win32' ? 'deltapatcher-engine.exe' : 'deltapatcher-engine'
const jobs = new Map<string, ChildProcessWithoutNullStreams>()

// every option the window passes; the main process refuses anything else
const ALLOWED_OPTIONS = new Set([
  '--source', '--trust-key', '--check', '--name', '--version', '--protect', '--leave-out', '--sign', '--no-htaccess',
  '--passphrase-stdin', '--port', '--to-folder', '--host', '--user', '--key', '--remote-root', '--dry-run',
  '--path', '--accept', '--forget'
])

function repoRoot(): string {
  // out/main -> out -> app -> the repository
  return join(__dirname, '..', '..', '..')
}

// A deltapatcher.ini beside the app (a portable copy a game maker ships, with source_url and
// trust_key filled in) is the one the engine uses; the engine itself lives inside the app's resources.
function portableSettings(): NodeJS.ProcessEnv {
  if (process.env.DELTAPATCHER_CONFIG) return {}
  // the portable .exe, an AppImage (its own path; execPath is inside the mounted image), or the installed app
  const dirs = [
    process.env.PORTABLE_EXECUTABLE_DIR,
    process.env.APPIMAGE ? dirname(process.env.APPIMAGE) : undefined,
    dirname(process.execPath)
  ].filter(Boolean) as string[]
  for (const d of dirs) {
    const ini = join(d, 'deltapatcher.ini')
    if (existsSync(ini)) return { DELTAPATCHER_CONFIG: ini }
  }
  return {}
}

export function engineCommand(): EngineCommand {
  // `describe` is shown in the window: no absolute paths of this computer in it
  if (app.isPackaged) {
    const packaged = join(process.resourcesPath, 'engine', EXE)
    if (existsSync(packaged)) return { cmd: packaged, pre: [], env: process.env, describe: `built in (${EXE})` }
  }
  const override = process.env.DELTAPATCHER_ENGINE
  if (override && existsSync(override)) return { cmd: override, pre: [], env: process.env, describe: 'DELTAPATCHER_ENGINE' }
  const local = join(repoRoot(), 'app', 'resources', 'engine', EXE)
  if (existsSync(local)) return { cmd: local, pre: [], env: process.env, describe: `app/resources/engine/${EXE}` }
  const root = repoRoot()
  const win = process.platform === 'win32'
  return {
    cmd: win ? 'py' : 'python3',
    pre: win ? ['-3', '-m', 'deltapatcher'] : ['-m', 'deltapatcher'],
    env: { ...process.env, PYTHONPATH: root },
    cwd: root,
    describe: 'python -m deltapatcher (from source)'
  }
}

export function runEngine(
  jobId: string,
  args: string[],
  passphrase: string | undefined,
  onEvent: (e: EngineEvent) => void
): Promise<EngineOutcome> {
  if (!Array.isArray(args) || args.length === 0 || !args.every((a) => typeof a === 'string')) {
    return Promise.resolve({ ok: false, data: {}, message: 'a malformed engine request', code: 'refused' })
  }
  if (!(ENGINE_COMMANDS as readonly string[]).includes(args[0])) {
    return Promise.resolve({ ok: false, data: {}, message: `the engine command ${args[0]} is not allowed`, code: 'refused' })
  }
  // only the options the window uses: nothing like serve --bind 0.0.0.0, --log or --header from here
  const bad = args.find((a) => a.startsWith('-') && !ALLOWED_OPTIONS.has(a.split('=')[0]))
  if (bad) {
    return Promise.resolve({ ok: false, data: {}, message: `the engine option ${bad.split('=')[0]} is not allowed`, code: 'refused' })
  }
  const e = engineCommand()
  const env = {
    ...e.env,
    ...portableSettings(),
    PYTHONIOENCODING: 'utf-8',
    PYTHONUNBUFFERED: '1',
    NO_COLOR: '1',
    // Windows: never run a program found in the working folder (a planted ssh-add.exe, say)
    NoDefaultCurrentDirectoryInExePath: '1'
  }
  let child: ChildProcessWithoutNullStreams
  try {
    // a built engine runs in the app's own data folder, never in a folder someone else could write to
    child = spawn(e.cmd, [...e.pre, '--json', ...args], { env, cwd: e.cwd ?? app.getPath('userData'), windowsHide: true })
  } catch (err) {
    return Promise.resolve({ ok: false, data: {}, message: `the engine could not start: ${String(err)}`, code: 'no-engine' })
  }
  jobs.set(jobId, child)
  child.stdin.on('error', () => undefined)
  if (passphrase !== undefined) {
    child.stdin.write(passphrase + '\n')
  }

  return new Promise<EngineOutcome>((resolve) => {
    let buf = ''
    let stderr = ''
    let outcome: EngineOutcome | null = null
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => {
      buf += chunk
      let nl: number
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim()
        buf = buf.slice(nl + 1)
        if (!line) continue
        let obj: Record<string, unknown>
        try {
          obj = JSON.parse(line)
        } catch {
          onEvent({ event: 'log', text: line, style: null })
          continue
        }
        if (obj.event === 'result') {
          outcome = { ok: true, data: (obj.data as Record<string, unknown>) ?? {} }
        } else if (obj.event === 'error') {
          outcome = {
            ok: false,
            data: (obj.data as Record<string, unknown>) ?? {},
            message: String(obj.message ?? 'failed'),
            code: String(obj.code ?? 'failed')
          }
        } else {
          onEvent(obj as unknown as EngineEvent)
        }
      }
    })
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk: string) => {
      stderr = (stderr + chunk).slice(-4000)
    })
    child.on('error', (err) => {
      outcome = outcome ?? { ok: false, data: {}, message: `the engine could not start (${e.describe}): ${err.message}`, code: 'no-engine' }
    })
    child.on('close', (code) => {
      jobs.delete(jobId)
      resolve(
        outcome ?? {
          ok: false,
          data: {},
          message: `the engine stopped without an answer (exit ${code})${stderr ? ': ' + stderr.trim().split('\n').slice(-3).join(' ') : ''}`,
          code: 'engine-stopped'
        }
      )
    })
  })
}

// "cancel" on the engine's standard input stops a download cleanly (the folder stays as it was)
export function cancelJob(jobId: string): void {
  const child = jobs.get(jobId)
  if (child && !child.stdin.destroyed) child.stdin.write('cancel\n')
}

// End a job and everything it started. A one-file engine (deltapatcher-cli, through DELTAPATCHER_ENGINE)
// is a small launcher that runs the program as its own child; on Windows killing the launcher leaves that
// child running (a local server would keep serving), so the whole tree is ended. Elsewhere the launcher
// passes the signal on.
function killTree(child: ChildProcessWithoutNullStreams): void {
  if (child.exitCode !== null || !child.pid) return
  if (process.platform === 'win32') {
    const taskkill = join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe')
    spawn(taskkill, ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
  } else {
    child.kill('SIGTERM')
  }
}

// Stop a job: close its standard input, which the engine takes as "stop" (the local server shuts down
// and the engine exits on its own); end the process tree only if it is still running two seconds later.
export function stopJob(jobId: string): void {
  const child = jobs.get(jobId)
  if (!child) return
  child.stdin.end()
  setTimeout(() => killTree(child), 2000).unref()
}

// the app is quitting: a download is cancelled (the folder stays as it was) and every engine sees its
// standard input close, which stops the local server
export function stopAll(): void {
  for (const child of jobs.values()) {
    if (!child.stdin.destroyed) {
      child.stdin.write('cancel\n')
      child.stdin.end()
    }
  }
  jobs.clear()
}
