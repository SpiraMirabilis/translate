import { useState, useCallback, useRef } from 'react'

export function useLocalStorage(key, defaultValue) {
  const [value, setValue] = useState(() => {
    try {
      const stored = localStorage.getItem(key)
      return stored !== null ? JSON.parse(stored) : defaultValue
    } catch {
      return defaultValue
    }
  })

  // Keep a ref to the current value so functional updaters always see latest
  const valueRef = useRef(value)
  valueRef.current = value

  const set = useCallback((newValue) => {
    const resolved = typeof newValue === 'function' ? newValue(valueRef.current) : newValue
    setValue(resolved)
    valueRef.current = resolved
    try {
      localStorage.setItem(key, JSON.stringify(resolved))
    } catch { /* quota exceeded etc — silently ignore */ }
  }, [key])

  return [value, set]
}
