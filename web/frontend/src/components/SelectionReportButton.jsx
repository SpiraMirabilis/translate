import { useCallback, useEffect, useState } from 'react'
import { Flag } from 'lucide-react'

const QUOTE_MAX = 500

/**
 * A "Report translation error" pill that appears over a text selection inside `containerRef`.
 *
 * This is what makes the quote on an error report nearly free to supply: the
 * reader highlights the wording that's wrong and the form opens already knowing
 * which passage they mean. Reporting without a selection still works — this is
 * a shortcut into the same modal, not the only way in.
 *
 * Props:
 *   containerRef - the prose subtree; selections outside it are ignored
 *   theme        - 'dark' | 'light' | 'sepia'
 *   onReport     - called with the selected text (capped) when the pill is hit
 */
export default function SelectionReportButton({ containerRef, theme = 'dark', onReport }) {
  const [pos, setPos] = useState(null)   // { top, left } in viewport coords
  const [text, setText] = useState('')

  const hide = useCallback(() => { setPos(null); setText('') }, [])

  const sync = useCallback(() => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return hide()

    const raw = sel.toString().trim()
    if (!raw) return hide()

    const range = sel.getRangeAt(0)
    const root = containerRef?.current
    // Both ends must be inside the prose — a selection that runs into the
    // toolbar or the comments isn't a quote from the chapter.
    if (!root || !root.contains(range.commonAncestorContainer)) return hide()

    const rect = range.getBoundingClientRect()
    if (!rect || (rect.width === 0 && rect.height === 0)) return hide()

    setText(raw.slice(0, QUOTE_MAX))
    setPos({
      top: Math.max(8, rect.top - 42),
      left: Math.min(
        Math.max(8, rect.left + rect.width / 2),
        window.innerWidth - 8,
      ),
    })
  }, [containerRef, hide])

  useEffect(() => {
    // mouseup/touchend catch the end of a drag; selectionchange catches
    // keyboard selection and, importantly, the click that clears one.
    const onEnd = () => setTimeout(sync, 0)
    const onSelectionChange = () => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed) hide()
    }
    const onKeyDown = (e) => { if (e.key === 'Escape') hide() }

    document.addEventListener('mouseup', onEnd)
    document.addEventListener('touchend', onEnd)
    document.addEventListener('selectionchange', onSelectionChange)
    document.addEventListener('keydown', onKeyDown)
    // The pill is positioned in viewport coords, so it has to go away rather
    // than drift when the page moves under it.
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    return () => {
      document.removeEventListener('mouseup', onEnd)
      document.removeEventListener('touchend', onEnd)
      document.removeEventListener('selectionchange', onSelectionChange)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('scroll', hide, true)
      window.removeEventListener('resize', hide)
    }
  }, [sync, hide])

  if (!pos || !text) return null

  const isDark = theme === 'dark'
  const pill = isDark
    ? 'bg-slate-700 text-slate-100 border-slate-600 hover:bg-slate-600'
    : theme === 'sepia'
      ? 'bg-amber-100 text-amber-900 border-amber-300 hover:bg-amber-200'
      : 'bg-white text-gray-700 border-stone-300 hover:bg-stone-100'

  return (
    <button
      type="button"
      // mousedown, not click: a click would first collapse the selection.
      onMouseDown={(e) => { e.preventDefault(); onReport(text); hide() }}
      style={{ top: pos.top, left: pos.left, transform: 'translateX(-50%)' }}
      className={`fixed z-50 flex items-center gap-1.5 rounded-full border px-3 py-1.5
                  text-xs font-medium shadow-lg transition-colors ${pill}`}
    >
      <Flag size={13} />
      Report translation error
    </button>
  )
}
