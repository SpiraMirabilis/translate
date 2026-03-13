/**
 * Copy text to clipboard with iOS Safari fallback.
 *
 * navigator.clipboard.writeText() on iOS can produce URL-encoded strings
 * for non-ASCII characters (e.g. Chinese). The textarea fallback avoids this.
 */
export function copyToClipboard(text) {
  // Use textarea fallback for iOS where clipboard API mangles Unicode
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
  if (isIOS || !navigator.clipboard?.writeText) {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, text.length)
    document.execCommand('copy')
    document.body.removeChild(ta)
    return Promise.resolve()
  }
  return navigator.clipboard.writeText(text)
}
