import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Transient flag/value with auto-reset — for "saved!" / "copied!" toasts.
 *
 *   const [flag, flash, clear] = useTransientFlag(ms)
 *
 * `flash()` sets the flag to `true` (or `flash(value)` to any truthy payload,
 * e.g. a message string or result object) and resets it to `false` after `ms`.
 * An optional per-call override `flash(value, overrideMs)` changes the delay
 * for that flash only. `clear()` cancels the pending timer and resets
 * immediately. The timer is cleaned up on unmount.
 */
export function useTransientFlag(ms) {
  const [flag, setFlag] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => () => clearTimeout(timerRef.current), [])

  const flash = useCallback((value = true, overrideMs) => {
    clearTimeout(timerRef.current)
    setFlag(value)
    timerRef.current = setTimeout(() => setFlag(false), overrideMs ?? ms)
  }, [ms])

  const clear = useCallback(() => {
    clearTimeout(timerRef.current)
    setFlag(false)
  }, [])

  return [flag, flash, clear]
}
