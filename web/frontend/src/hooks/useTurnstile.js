import { useEffect, useRef, useState, useCallback } from 'react'

const SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

function ensureScript() {
  if (typeof document === 'undefined') return null
  let existing = document.querySelector('script[src*="turnstile"]')
  if (existing) return existing
  const script = document.createElement('script')
  script.src = SCRIPT_URL
  script.async = true
  document.head.appendChild(script)
  return script
}

/**
 * Render a Turnstile widget into a container ref.
 *
 * Returns { token, reset, ref } — attach `ref` to a div, read `token` after
 * the user solves the challenge, call `reset()` after a failed submit so
 * the widget re-prompts.
 */
export function useTurnstile(siteKey, themeName = 'auto') {
  const containerRef = useRef(null)
  const widgetIdRef = useRef(null)
  const [token, setToken] = useState('')

  const render = useCallback(() => {
    if (!siteKey || !containerRef.current || widgetIdRef.current != null) return
    if (!window.turnstile) return
    widgetIdRef.current = window.turnstile.render(containerRef.current, {
      sitekey: siteKey,
      callback: (t) => setToken(t),
      'expired-callback': () => setToken(''),
      theme: themeName,
    })
  }, [siteKey, themeName])

  useEffect(() => {
    if (!siteKey) return
    const script = ensureScript()
    if (window.turnstile) {
      render()
      return
    }
    if (script) {
      script.addEventListener('load', render)
      return () => script.removeEventListener('load', render)
    }
  }, [siteKey, render])

  useEffect(() => {
    return () => {
      if (widgetIdRef.current != null && window.turnstile) {
        try { window.turnstile.remove(widgetIdRef.current) } catch { /* ignore */ }
        widgetIdRef.current = null
      }
    }
  }, [])

  const reset = useCallback(() => {
    if (widgetIdRef.current != null && window.turnstile) {
      window.turnstile.reset(widgetIdRef.current)
      setToken('')
    }
  }, [])

  return { ref: containerRef, token, reset }
}
