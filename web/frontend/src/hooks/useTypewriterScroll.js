import { useEffect, useRef } from 'react'

/**
 * Typewriter scrolling for focus mode: keep the caret parked at ~45% of the
 * scroll container's height while typing or moving the selection.
 * rAF-throttled; no-op when disabled or the editor/container are missing.
 */
export function useTypewriterScroll(editor, enabled, containerRef) {
  const rafRef = useRef(null)

  useEffect(() => {
    if (!editor || !enabled) return undefined

    const recenter = () => {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(() => {
        const container = containerRef.current
        if (!container || editor.isDestroyed) return
        let coords
        try {
          coords = editor.view.coordsAtPos(editor.state.selection.head)
        } catch {
          return // position momentarily invalid mid-transaction
        }
        const rect = container.getBoundingClientRect()
        const target = rect.top + rect.height * 0.45
        const delta = coords.top - target
        if (Math.abs(delta) < 4) return
        container.scrollTo({
          top: container.scrollTop + delta,
          behavior: Math.abs(delta) < 120 ? 'smooth' : 'auto',
        })
      })
    }

    editor.on('selectionUpdate', recenter)
    editor.on('update', recenter)
    recenter()
    return () => {
      editor.off('selectionUpdate', recenter)
      editor.off('update', recenter)
      cancelAnimationFrame(rafRef.current)
    }
  }, [editor, enabled, containerRef])
}
