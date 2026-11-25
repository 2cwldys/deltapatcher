// What the window says a check or an update downloads (src/renderer/src/store/download.ts): the smaller amount when
// archives are rebuilt from what is here, the whole files from a result without it, and the line on the archives.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { rebuiltLine, toDownload } from '../src/renderer/src/store/download.ts'

const human = (n) => `${n} bytes`

test('the bytes fetched, when the engine says them', () => {
  assert.equal(toDownload({ download_bytes: 1000, fetch_bytes: 30, rebuilt: 1 }), 30)
  assert.equal(toDownload({ download_bytes: 1000, fetch_bytes: 0, rebuilt: 1 }), 0)
  assert.equal(toDownload({ download_bytes: 1000, fetch_bytes: 1000, rebuilt: 0 }), 1000)
  assert.equal(toDownload({ download_bytes: 1000 }), 1000)
})

test('a short line on the archives rebuilt, none without them', () => {
  assert.equal(rebuiltLine({ download_bytes: 1000, fetch_bytes: 30, rebuilt: 1 }, human), '1 archive rebuilt from what is here: 1000 bytes whole.')
  assert.equal(rebuiltLine({ download_bytes: 1000, fetch_bytes: 30, rebuilt: 2 }, human), '2 archives rebuilt from what is here: 1000 bytes whole.')
  assert.equal(rebuiltLine({ download_bytes: 1000, fetch_bytes: 1000, rebuilt: 0 }, human), '')
  assert.equal(rebuiltLine({ download_bytes: 1000 }, human), '')
})
