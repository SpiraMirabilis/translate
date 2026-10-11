// Server-inlined JSON payloads: <script type="application/json" id="…"> blocks
// the backend splices into the SPA shell (web/app_factory.py) so a page can
// render without a second round trip for its data.
//
// Read once per page load and removed from the DOM. `at` is when the shell
// arrived, so a react-query consumer can pass it as initialDataUpdatedAt and
// let ordinary staleness decide when to refetch.
const taken = new Map()
const loadedAt = Date.now()

export function takeInlineJson(id) {
  if (taken.has(id)) return taken.get(id)
  let out = null
  try {
    const el = typeof document !== 'undefined' ? document.getElementById(id) : null
    if (el) {
      out = { data: JSON.parse(el.textContent), at: loadedAt }
      el.remove()
    }
  } catch {
    out = null
  }
  taken.set(id, out)
  return out
}
