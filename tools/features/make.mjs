// The README's features picture, docs/images/features.png, in the app's colours: how a release travels, then
// every feature with its group, title, line and icon (from the app's own icon set). A feature added to
// DeltaPatcher gets its entry in GROUPS, and the picture is made again.
//
//   node tools/features/make.mjs [--out DIR] [--browser PATH]
//
// Needs the app's packages (npm ci in app/: the icons) and tools/readme_preview's (npm ci there: the browser
// driver). The picture is taken with Edge, Chrome or Chromium already on this computer (--browser names
// another), at twice the README's width, with a clear background so it suits GitHub's light and dark themes.
// DIR: write it there instead of docs/images.

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const ICONS = join(ROOT, 'app', 'node_modules', 'lucide-react', 'dist', 'esm', 'icons')

// ---- what the picture says ----------------------------------------------------------------------------------
// colour: amber (what the publisher does), teal (what players get), slate (around it all)

const FLOW = [
  { icon: 'folder-open', colour: 'slate', name: 'Your folder', line: 'pk3, zip, maps, any file' },
  { icon: 'package', colour: 'amber', name: 'Release', line: 'only what changed' },
  { icon: 'server', colour: 'amber', name: 'Any host', line: 'a VPS, any web server' },
  { icon: 'monitor', colour: 'teal', name: 'Players', line: 'every file checked' },
  { icon: 'users', colour: 'teal', name: 'Each other', line: 'torrents, optional', both: true }
]

// [icon, title, line, colour of its icon when not the group's]
const GROUPS = [
  {
    name: 'Release', icon: 'package', colour: 'amber',
    line: 'You publish a folder.\nOnly what changed goes up.',
    features: [
      ['server', 'One button to your server', 'Release sends only the missing files over SFTP and switches the new version in last.'],
      ['globe', 'Host it anywhere', 'Plain static files: Caddy, nginx, Apache, XAMPP, any static host, a share, or the built-in server.'],
      ['shield-check', 'Signed releases', 'Optional Ed25519 signatures: updaters refuse any release your key did not sign.'],
      ['layers', 'Shadow patches', 'Release the same version again with fixes: only the changed files travel.'],
      ['mouse-pointer-click', 'Pick, do not type', 'Leave folders out, or let players keep their own settings and saves, by picking them.'],
      ['scroll-text', 'Patch notes', 'Players see what a release changed, file by file, with your notes; file names only if you allow it.']
    ]
  },
  {
    name: 'Update', icon: 'download', colour: 'teal',
    line: 'Players get only the files\ntheir folder lacks.',
    features: [
      ['file-down', 'Only what changed', 'Players download just the files their folder lacks, files you deleted are removed (never their own), and a cut download resumes.'],
      ['rotate-ccw', 'Safe updates', 'Every file is checked before it is used. A failed update is undone, and Roll back undoes the last one.'],
      ['bell', 'Updates while you play', 'It checks every hour from the tray, says what is waiting, and can update by itself.'],
      ['folders', 'Several projects', 'Every game, mod or server you keep up to date or release, each with its own settings. Update all runs three at once.']
    ]
  },
  {
    name: 'Share', icon: 'share-2', colour: 'teal',
    line: 'One file sets players up.\nPlayers can share too.',
    features: [
      ['file-key', 'Project presets', 'Share projects as one .deltapatch or .html file: players open it and are set up, signed so they know it is yours. Private ones are for you.'],
      ['refresh-cw', 'Presets that follow you', 'Change a project later and players are asked before anything changes; one-off files stay possible.'],
      ['network', 'Torrents, if you want them', 'Players can download from each other and seed, no server needed; off unless you turn it on, and anyone can refuse it.']
    ]
  },
  {
    name: 'Look after it', icon: 'gauge', colour: 'slate',
    line: 'Numbers, backups, a stop\nbutton, and every way to run it.',
    features: [
      ['chart-column', 'Statistics for publishers', "How many players check and download, and which release they are on, counted from your own web server's log."],
      ['archive', 'Backups', 'Dated copies of the live release on your computer, by hand or on a schedule, and one step to put one back.'],
      ['octagon-x', 'Force stop', 'One red button ends every update, check, release, download and seed at once, and nothing starts until you lift it.', 'red'],
      ['terminal', 'App and command line', 'A clean window, a menu of plain questions, and commands for scripts and launchers.'],
      ['container', 'In your containers', 'Docker images and Pterodactyl eggs can be brought up to date at every start, from an address or a project file.']
    ]
  }
]

// ---- the page -----------------------------------------------------------------------------------------------

function options(argv) {
  const o = { out: join(ROOT, 'docs', 'images'), browser: '' }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--out') o.out = resolve(argv[++i])
    else if (a === '--browser') o.browser = resolve(argv[++i])
    else if (a === '--help' || a === '-h') {
      console.log('node tools/features/make.mjs [--out DIR] [--browser PATH]')
      process.exit(0)
    } else {
      console.error('unknown argument: ' + a)
      process.exit(2)
    }
  }
  return o
}

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

// an icon of the app's set (lucide) as SVG: its shapes read from the module that draws it in the app
function icon(name, size) {
  const file = join(ICONS, name + '.js')
  if (!existsSync(file)) throw new Error('no icon ' + name + ' (npm ci in app/ first)')
  const src = readFileSync(file, 'utf8')
  const start = src.indexOf('[', src.indexOf('createLucideIcon('))
  const end = src.lastIndexOf(']);')
  const nodes = new Function('return ' + src.slice(start, end + 1))()
  const shapes = nodes.map(([tag, attrs]) => '<' + tag + Object.entries(attrs)
    .filter(([k]) => k !== 'key').map(([k, v]) => ' ' + k + '="' + esc(v) + '"').join('') + '/>').join('')
  return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '" viewBox="0 0 24 24" ' +
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + shapes + '</svg>'
}

const CSS = `
:root {
  --text: #f0f4f8; --muted: #93a1b5; --panel: #131a24; --line: #222c3b; --arrow: #5b6a80;
  --amber: #f7c06f; --amber-soft: rgba(242, 163, 58, .15);
  --teal: #3fd1bd; --teal-soft: rgba(25, 179, 160, .16);
  --slate: #b4c0d1; --slate-soft: rgba(133, 147, 168, .16);
  --red: #ff7b72; --red-soft: rgba(255, 123, 114, .15);
}
html, body { margin: 0; background: transparent; }
body { font-family: "Segoe UI", Inter, system-ui, sans-serif; color: var(--text); }
#art { width: 880px; padding: 2px; box-sizing: border-box; display: flex; flex-direction: column; gap: 12px; }
.amber { --c: var(--amber); --soft: var(--amber-soft); }
.teal { --c: var(--teal); --soft: var(--teal-soft); }
.slate { --c: var(--slate); --soft: var(--slate-soft); }
.red { --c: var(--red); --soft: var(--red-soft); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; }

.flow { display: flex; align-items: flex-start; justify-content: space-between; padding: 18px 20px 16px; }
.step { width: 140px; text-align: center; }
.tile { width: 50px; height: 50px; margin: 0 auto 9px; border-radius: 14px; display: grid; place-items: center;
        background: var(--soft); color: var(--c); }
.step .name { font-weight: 600; font-size: 14px; }
.step .line { font-size: 11.5px; color: var(--muted); margin-top: 3px; line-height: 1.35; }
.arrow { margin-top: 15px; color: var(--arrow); flex: 0 0 auto; }
.arrow.both { color: var(--amber); }

.band { display: grid; grid-template-columns: 176px 1fr; overflow: hidden; }
.side { padding: 18px 16px; border-right: 1px solid var(--line);
        background: linear-gradient(165deg, var(--soft), transparent 75%); }
.side .big { width: 44px; height: 44px; border-radius: 12px; display: grid; place-items: center;
             background: var(--soft); color: var(--c); margin-bottom: 12px; }
.side .label { font-size: 12px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: var(--c); }
.side .line { font-size: 12.5px; color: var(--muted); line-height: 1.45; margin-top: 6px; white-space: pre-line; }
.items { display: grid; grid-template-columns: 1fr 1fr; column-gap: 20px; row-gap: 2px; padding: 10px 18px; }
.item { display: grid; grid-template-columns: 30px 1fr; column-gap: 11px; padding: 8px 0; }
.item.wide { grid-column: span 2; }
.item .ic { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center;
            background: var(--soft); color: var(--c); }
.item .t { font-weight: 600; font-size: 14px; line-height: 1.3; margin-top: 1px; }
.item .d { font-size: 12.5px; color: var(--muted); line-height: 1.45; margin-top: 2px; }
`

function arrow(both) {
  if (!both) return '<div class="arrow">' + icon('arrow-right', 20) + '</div>'
  return '<div class="arrow both"><svg xmlns="http://www.w3.org/2000/svg" width="34" height="20" viewBox="0 0 34 20" ' +
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M4 10h26" stroke-dasharray="3 4"/><path d="m8 6-4 4 4 4"/><path d="m26 6 4 4-4 4"/></svg></div>'
}

function page() {
  const flow = FLOW.map((s, i) => (i ? arrow(s.both) : '') +
    '<div class="step ' + s.colour + '"><div class="tile">' + icon(s.icon, 24) + '</div>' +
    '<div class="name">' + esc(s.name) + '</div><div class="line">' + esc(s.line) + '</div></div>').join('')
  const bands = GROUPS.map((g) => {
    const items = g.features.map(([ic, title, text, colour], i) => {
      const wide = i === g.features.length - 1 && g.features.length % 2 === 1      // an odd last one spans both
      return '<div class="item' + (wide ? ' wide' : '') + (colour ? ' ' + colour : '') + '">' +
        '<div class="ic">' + icon(ic, 17) + '</div><div><div class="t">' + esc(title) + '</div>' +
        '<div class="d">' + esc(text) + '</div></div></div>'
    }).join('')
    return '<div class="card band ' + g.colour + '"><div class="side"><div class="big">' + icon(g.icon, 24) + '</div>' +
      '<div class="label">' + esc(g.name) + '</div><div class="line">' + esc(g.line) + '</div></div>' +
      '<div class="items">' + items + '</div></div>'
  }).join('')
  return '<!doctype html><html><head><meta charset="utf-8"><style>' + CSS + '</style></head>' +
    '<body><div id="art"><div class="card flow">' + flow + '</div>' + bands + '</div></body></html>'
}

// ---- the picture --------------------------------------------------------------------------------------------

function browserPath(given) {
  if (given) return given
  const env = process.env
  return [
    join(env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    join(env.ProgramFiles || 'C:\\Program Files', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    join(env.ProgramFiles || 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe'),
    join(env.LOCALAPPDATA || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'
  ].find((p) => p && existsSync(p)) || ''
}

async function main() {
  const o = options(process.argv.slice(2))
  const html = page()
  const exe = browserPath(o.browser)
  if (!exe) {
    console.error('no Edge, Chrome or Chromium found: --browser names one')
    process.exit(1)
  }
  const req = createRequire(join(ROOT, 'tools', 'readme_preview', 'package.json'))
  let mod
  try {
    mod = req('puppeteer-core')
  } catch (e) {
    console.error('the browser driver is missing: npm ci in tools/readme_preview first')
    process.exit(1)
  }
  const puppeteer = mod.default || mod
  mkdirSync(o.out, { recursive: true })
  const profile = mkdtempSync(join(tmpdir(), 'dp-features-'))
  const browser = await puppeteer.launch({ executablePath: exe, headless: true, userDataDir: profile })
  try {
    const tab = await browser.newPage()
    await tab.setViewport({ width: 900, height: 900, deviceScaleFactor: 2 })
    await tab.setContent(html, { waitUntil: 'load' })
    const png = join(o.out, 'features.png')
    await (await tab.$('#art')).screenshot({ path: png, omitBackground: true })
    console.log(png)
  } finally {
    await browser.close()
    rmSync(profile, { recursive: true, force: true })
  }
}

main().catch((e) => {
  console.error(e && e.stack ? e.stack : String(e))
  process.exit(1)
})
