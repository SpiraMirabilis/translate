// Dev convenience: disable browser caching of API calls + mutable assets so
// that edits to books / chapters / covers / illustrations show up immediately
// without fighting the HTTP cache. Toggled from the Settings page.
//
// Frontend-only + localStorage-backed on purpose: the flag must apply the
// instant it's flipped (no service restart, no settings round-trip) and only
// affects this browser.
const KEY = 't9_no_cache'

// A nonce that changes on every full page load. When no-cache is on we append
// it to asset URLs so the browser refetches them after each reload instead of
// serving a stale cached image.
const LOAD_NONCE = String(Date.now())

export function isNoCache() {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}

export function setNoCache(on) {
  try {
    if (on) localStorage.setItem(KEY, '1')
    else localStorage.removeItem(KEY)
  } catch { /* localStorage unavailable — ignore */ }
}

// Append a cache-busting query param to an asset URL when no-cache is enabled.
// No-ops on empty/falsy URLs and inline data:/blob: URIs.
export function bustUrl(url) {
  if (!url || !isNoCache()) return url
  if (url.startsWith('data:') || url.startsWith('blob:')) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}_nc=${LOAD_NONCE}`
}
