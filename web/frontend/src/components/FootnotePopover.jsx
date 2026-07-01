import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'

// Small modeless, non-blocking footnote popover. Renders a fixed-position box
// next to the clicked marker (no backdrop, so the page stays interactive).
// Dismisses on outside click, Escape, scroll, and resize.
export default function FootnotePopover({ footnote, theme, onClose }) {
  const boxRef = useRef(null)
  const [pos, setPos] = useState(null)  // { left, top } in viewport coords, or null until measured

  const isDark = theme === 'dark'
  const isSepia = theme === 'sepia'

  // Position relative to the marker rect: below by default, flip above if it would
  // overflow the bottom. Clamp horizontally to the viewport.
  useLayoutEffect(() => {
    const rect = footnote?.rect
    const box = boxRef.current
    if (!rect || !box) return
    const margin = 8
    const { width, height } = box.getBoundingClientRect()
    let left = rect.left
    left = Math.max(margin, Math.min(left, window.innerWidth - width - margin))
    let top = rect.bottom + 6
    if (top + height > window.innerHeight - margin) {
      const above = rect.top - height - 6
      top = above >= margin ? above : Math.max(margin, window.innerHeight - height - margin)
    }
    setPos({ left, top })
  }, [footnote])

  // Dismiss handlers. Outside click uses mousedown so it fires before any new
  // marker's click re-opens the popover.
  useEffect(() => {
    function onDown(e) {
      if (boxRef.current && !boxRef.current.contains(e.target) &&
          !e.target.closest?.('.footnote-ref')) {
        onClose()
      }
    }
    function onScrollOrResize() { onClose() }
    function onKey(e) { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScrollOrResize, true)
    window.addEventListener('resize', onScrollOrResize)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScrollOrResize, true)
      window.removeEventListener('resize', onScrollOrResize)
    }
  }, [onClose])

  if (!footnote) return null

  const boxClass = isDark
    ? 'bg-slate-800 border-slate-600 text-slate-200'
    : isSepia
      ? 'bg-amber-50 border-amber-300 text-amber-900'
      : 'bg-white border-stone-300 text-gray-800'

  return (
    <div
      ref={boxRef}
      role="dialog"
      className={`fixed z-50 max-w-[320px] rounded-lg border shadow-xl text-sm leading-relaxed ${boxClass}`}
      style={{
        left: pos ? pos.left : footnote.rect.left,
        top: pos ? pos.top : footnote.rect.bottom + 6,
        visibility: pos ? 'visible' : 'hidden',
      }}
    >
      <div className="flex items-start gap-2 p-3">
        <span className="font-semibold shrink-0 text-indigo-500">[{footnote.n}]</span>
        <span className="min-w-0 break-words">{footnote.text}</span>
        <button
          onClick={onClose}
          aria-label="Close footnote"
          className={`shrink-0 -mr-1 -mt-0.5 rounded p-0.5 ${isDark ? 'hover:bg-slate-700' : 'hover:bg-black/10'}`}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
