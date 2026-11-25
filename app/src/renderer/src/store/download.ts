// What a check or an update downloads, as the window says it: the bytes it fetches, which are fewer than the whole
// files when archives are rebuilt from what the folder has (only the files inside them that changed travel), and how
// many archives that is. Nothing here uses the window, so a test can run it as it is.

// the amounts of the engine's update and check results
export interface DownloadAmounts {
  download_bytes: number
  fetch_bytes?: number
  rebuilt?: number
}

// the bytes it downloads: fetch_bytes, else (a result without it) the whole files
export function toDownload(r: DownloadAmounts): number {
  return typeof r.fetch_bytes === 'number' && r.fetch_bytes >= 0 ? r.fetch_bytes : r.download_bytes
}

// one short sentence on the archives rebuilt from what is here, '' when there are none; `human` writes a size
export function rebuiltLine(r: DownloadAmounts, human: (n: number) => string): string {
  const n = r.rebuilt ?? 0
  if (n <= 0) return ''
  return `${n === 1 ? '1 archive' : `${n} archives`} rebuilt from what is here: ${human(r.download_bytes)} whole.`
}
