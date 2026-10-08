// Remove hashed assets from dist/assets that are older than MAX_AGE_DAYS and
// not referenced by the current index.html. Runs after `vite build`
// (package.json), because vite.config.js sets emptyOutDir: false so that a
// crawler rendering last week's HTML still finds last week's chunks.
import { readdirSync, readFileSync, statSync, unlinkSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const MAX_AGE_DAYS = Number(process.env.PRUNE_ASSETS_DAYS || 30)
const dist = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist')
const assets = join(dist, 'assets')
const index = readFileSync(join(dist, 'index.html'), 'utf8')
const cutoff = Date.now() - MAX_AGE_DAYS * 86400 * 1000

let removed = 0
for (const name of readdirSync(assets)) {
  if (index.includes(`/assets/${name}`)) continue
  const path = join(assets, name)
  if (statSync(path).mtimeMs < cutoff) { unlinkSync(path); removed++ }
}
console.log(`prune-assets: removed ${removed} asset(s) older than ${MAX_AGE_DAYS} days`)
