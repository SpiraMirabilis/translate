import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'

// Floor for the scroll-capped placement: below this the box is too small to be
// worth reading, and overlapping the anchor is the lesser evil.
const MIN_HEIGHT = 80

// Small modeless, non-blocking popover. Renders a fixed-position box next to
// the anchor that opened it (no backdrop, so the page stays interactive).
// Dismisses on outside click, Escape, scroll, and resize.
//
// Serves two anchors: footnote markers ({ n, text, rect }, shown as "[n]") and
// highlighted glossary terms ({ label, text, rect }, shown as the term itself).
export default function FootnotePopover({ footnote, theme, onClose, onMouseEnter, onMouseLeave }) {
  const boxRef = useRef(null)
  const [pos, setPos] = useState(null)  // { left, top, maxHeight } in viewport coords, or null until measured

  const isDark = theme === 'dark'
  const isSepia = theme === 'sepia'

  // Position relative to the marker rect: below by default, flip above if it would
  // overflow the bottom. Clamp horizontally to the viewport.
  //
  // A note too tall for either side is NOT allowed to settle on top of its own
  // anchor: for a hover-opened term note that would pull the pointer off the
  // term, closing the popover, which puts the pointer back on the term, which
  // reopens it — a visible flicker loop. Instead it takes the roomier side and
  // scrolls inside a capped height.
  useLayoutEffect(() => {
    const rect = footnote?.rect
    const box = boxRef.current
    if (!rect || !box) return
    const margin = 8
    const gap = 6
    // Measure unconstrained — a previous placement may have left a cap on the box.
    box.style.maxHeight = ''
    const { width, height } = box.getBoundingClientRect()
    const left = Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin))

    const spaceBelow = window.innerHeight - rect.bottom - gap - margin
    const spaceAbove = rect.top - gap - margin

    if (height <= spaceBelow) {
      setPos({ left, top: rect.bottom + gap, maxHeight: null })
    } else if (height <= spaceAbove) {
      setPos({ left, top: rect.top - height - gap, maxHeight: null })
    } else if (spaceAbove > spaceBelow) {
      const maxHeight = Math.max(MIN_HEIGHT, spaceAbove)
      setPos({ left, top: Math.max(margin, rect.top - gap - maxHeight), maxHeight })
    } else {
      setPos({ left, top: rect.bottom + gap, maxHeight: Math.max(MIN_HEIGHT, spaceBelow) })
    }
  }, [footnote])

  // Dismiss handlers. Outside click uses mousedown so it fires before any new
  // marker's click re-opens the popover.
  useEffect(() => {
    function onDown(e) {
      if (boxRef.current && !boxRef.current.contains(e.target) &&
          !e.target.closest?.('.footnote-ref, .term-note')) {
        onClose()
      }
    }
    // Capture-phase scroll sees the popover's own inner scrolling too; only a
    // scroll that moves the anchor should dismiss it.
    function onScrollOrResize(e) {
      if (e?.target && boxRef.current?.contains(e.target)) return
      onClose()
    }
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
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className={`fixed z-50 max-w-[320px] rounded-lg border shadow-xl text-sm leading-relaxed overscroll-contain ${boxClass}`}
      style={{
        left: pos ? pos.left : footnote.rect.left,
        top: pos ? pos.top : footnote.rect.bottom + 6,
        maxHeight: pos?.maxHeight || undefined,
        overflowY: pos?.maxHeight ? 'auto' : undefined,
        visibility: pos ? 'visible' : 'hidden',
      }}
    >
      <div className="flex items-start gap-2 p-3">
        <span className="font-semibold shrink-0 text-indigo-500">
          {footnote.label ? footnote.label : `[${footnote.n}]`}
        </span>
        <span className="min-w-0 break-words">{footnote.text}</span>
        <button
          onClick={onClose}
          aria-label="Close"
          className={`shrink-0 sticky top-0 -mr-1 -mt-0.5 rounded p-0.5 ${isDark ? 'hover:bg-slate-700' : 'hover:bg-black/10'}`}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
