import { useEffect, useCallback } from 'react'
import { useBlocker } from 'react-router-dom'

/**
 * Guard unsaved editor changes against tab close and in-app navigation.
 * Extracted from ChapterEditor; shared with WriteEditor.
 *
 * Only blocks when leaving the current path — same-path query-param changes
 * (active line, modals, search) are never blocked.
 */
export function useUnsavedGuard(dirty) {
  useEffect(() => {
    const handler = (e) => {
      if (dirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  const shouldBlock = useCallback(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
    [dirty]
  )
  const blocker = useBlocker(shouldBlock)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (window.confirm('Discard unsaved changes?')) blocker.proceed()
      else blocker.reset()
    }
  }, [blocker])
}
