// Copies what the app needs from the rest of the repository before a build:
//   - the icons (deltapatcher/assets, docs/images/logo.png) into build/ and the renderer's public folder
//   - the engine (the folder dist/engine/, made by build.ps1 / build.sh) into resources/engine/
// node scripts/prepare.mjs [--need-engine]   (--need-engine: stop when there is no built engine)
import { copyFileSync, cpSync, existsSync, mkdirSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const app = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = resolve(app, '..')

function copy(from, to) {
  if (!existsSync(from)) {
    console.error(`prepare: ${from} is missing (clone with Git LFS, or run tools/make_icon.py)`)
    process.exit(1)
  }
  mkdirSync(dirname(to), { recursive: true })
  copyFileSync(from, to)
}

copy(join(repo, 'deltapatcher', 'assets', 'deltapatcher.ico'), join(app, 'build', 'icon.ico'))
copy(join(repo, 'docs', 'images', 'logo.png'), join(app, 'build', 'icon.png'))
copy(join(repo, 'docs', 'images', 'logo.png'), join(app, 'src', 'renderer', 'public', 'logo.png'))

// the whole folder, replaced each time so nothing from an older engine is left in it
const exe = process.platform === 'win32' ? 'deltapatcher-engine.exe' : 'deltapatcher-engine'
const engineDir = join(repo, 'dist', 'engine')
const to = join(app, 'resources', 'engine')
rmSync(to, { recursive: true, force: true })
mkdirSync(to, { recursive: true })
if (existsSync(join(engineDir, exe))) {
  cpSync(engineDir, to, { recursive: true })
  console.log(`prepare: engine dist/engine/${exe}`)
} else if (process.argv.includes('--need-engine')) {
  console.error(`prepare: no engine at dist/engine/${exe}: build it first (build.ps1 or build.sh at the repository's root)`)
  process.exit(1)
} else {
  console.log('prepare: no built engine; the app runs the Python package from source (python -m deltapatcher)')
}
