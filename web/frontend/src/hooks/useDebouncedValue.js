import { useState, useEffect, useRef } from 'react'

/**
 * Debounced value — returns `value` after it has stopped changing for
 * `delay` ms. The very first change after mount is applied immediately
 * (nice for initial loads where the source value arrives async).
 */
export function useDebouncedValue(value, delay) {
  const [debounced, setDebounced] = useState(value)
  const isFirst = useRef(true)
  useEffect(() => {
    if (isFirst.current) { isFirst.current = false; setDebounced(value); return }
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}
