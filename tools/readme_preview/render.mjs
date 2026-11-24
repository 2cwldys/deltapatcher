// README.md, or any guide, previewed the way GitHub shows it: GitHub's own markdown stylesheet, mermaid
// diagrams drawn, pictures and badges loaded. Saved as an HTML page and a full-page PNG.
//
//   cd tools/readme_preview && npm ci                         (once)
//   node tools/readme_preview/render.mjs [FILE.md] [--light] [--out DIR] [--browser PATH]
//
// FILE.md: the repository's README.md when left out. --light: GitHub's light theme (dark otherwise).
// DIR: dist/preview when left out. The picture is taken with Edge, Chrome or Chromium already on this
// computer (Edge comes with Windows; --browser names another); without one, only the HTML page is written.
// Relative links and pictures resolve from the file's own folder, as on GitHub.

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)
const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')

function options(argv) {
  const o = { file: join(ROOT, 'README.md'), light: false, out: join(ROOT, 'dist', 'preview'), browser: '' }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--light') o.light = true
    else if (a === '--out') o.out = resolve(argv[++i])
    else if (a === '--browser') o.browser = resolve(argv[++i])
    else if (a === '--help' || a === '-h') {
      console.log('node tools/readme_preview/render.mjs [FILE.md] [--light] [--out DIR] [--browser PATH]')
      process.exit(0)
    } else o.file = resolve(a)
  }
  return o
}

function findBrowser() {
  const env = process.env
  const places = {
    win32: [
      join(env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      join(env.ProgramFiles || 'C:\\Program Files', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      join(env.ProgramFiles || 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe'),
      join(env.LOCALAPPDATA || '', 'Google', 'Chrome', 'Application', 'chrome.exe')
    ],
    darwin: [
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
      '/Applications/Chromium.app/Contents/MacOS/Chromium'
    ],
    linux: ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser',
      '/usr/bin/microsoft-edge', '/snap/bin/chromium']
  }
  return (places[process.platform] || places.linux).find((p) => p && existsSync(p)) || ''
}

function page(o) {
  const { marked } = require('marked')
  const renderer = new marked.Renderer()
  const code = renderer.code.bind(renderer)
  renderer.code = (text, lang, escaped) =>
    lang === 'mermaid' ? `<pre class="mermaid">${text.replace(/</g, '&lt;')}</pre>` : code(text, lang, escaped)
  const body = marked.parse(readFileSync(o.file, 'utf8'), { gfm: true, renderer })
  const css = readFileSync(require.resolve(`github-markdown-css/github-markdown-${o.light ? 'light' : 'dark'}.css`), 'utf8')
  const mermaid = pathToFileURL(require.resolve('mermaid/dist/mermaid.min.js')).href
  const base = pathToFileURL(dirname(o.file) + '/').href
  const [bg, line] = o.light ? ['#ffffff', '#d0d7de'] : ['#0d1117', '#30363d']
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>${basename(o.file)}</title><base href="${base}">
<style>${css}
html, body { background: ${bg}; margin: 0; }
.box { max-width: 1012px; margin: 24px auto; border: 1px solid ${line}; border-radius: 6px; }
.markdown-body { padding: 32px; }
</style></head>
<body><div class="box"><article class="markdown-body">${body}</article></div>
<script src="${mermaid}"></script>
<script>mermaid.initialize({ startOnLoad: true, theme: '${o.light ? 'default' : 'dark'}' })</script>
</body></html>
`
}

async function picture(html, png, browserPath) {
  const puppeteer = (await import('puppeteer-core')).default
  const profile = mkdtempSync(join(tmpdir(), 'readme-preview-'))
  const browser = await puppeteer.launch({ executablePath: browserPath, headless: true, userDataDir: profile,
    args: ['--hide-scrollbars', '--allow-file-access-from-files'] })
  try {
    const tab = await browser.newPage()
    await tab.setViewport({ width: 1100, height: 900 })
    await tab.goto(pathToFileURL(html).href, { waitUntil: 'networkidle0', timeout: 60000 })
    await tab.waitForFunction(() => [...document.querySelectorAll('pre.mermaid')].every((p) => p.querySelector('svg')),
      { timeout: 30000 }).catch(() => console.log('a mermaid diagram was not drawn in time; the picture shows it as text'))
    await tab.screenshot({ path: png, fullPage: true })
  } finally {
    await browser.close()
    rmSync(profile, { recursive: true, force: true })
  }
}

const o = options(process.argv.slice(2))
if (!existsSync(o.file)) {
  console.error(`no such file: ${o.file}`)
  process.exit(1)
}
mkdirSync(o.out, { recursive: true })
const html = join(o.out, 'preview.html')
writeFileSync(html, page(o))
console.log(html)
const browserPath = o.browser || findBrowser()
if (!browserPath) {
  console.log('no Edge, Chrome or Chromium found (--browser names one): open the HTML page in a browser instead')
} else {
  const png = join(o.out, 'preview.png')
  await picture(html, png, browserPath)
  console.log(png)
}
