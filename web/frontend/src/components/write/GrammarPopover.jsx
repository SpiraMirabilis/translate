import { useEffect, useRef, useState } from 'react'
import { X, BookPlus, Globe, EyeOff } from 'lucide-react'
import { KIND_LABEL, KIND_DOT } from '../../lib/grammarKinds'

const EST_WIDTH = 320
const EST_HEIGHT = 190

/**
 * Fixed-position popover for one grammar/polish issue, anchored below its
 * squiggle (rect from coordsAtPos). data-esc-guard keeps WriteEditor's Esc
 * handler from exiting focus mode while it's open; click-away closes without
 * a backdrop so the next editor click isn't eaten.
 */
export default function GrammarPopover({ active, onApply, onDismiss, onClose, onAddToDictionary, onIgnoreRule }) {
  const { issue, rect } = active
  const ref = useRef(null)
  const [added, setAdded] = useState(false)

  useEffect(() => { setAdded(false) }, [issue.id])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    const onMouseDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose()
    }
    window.addEventListener('keydown', onKey, true)
    document.addEventListener('mousedown', onMouseDown)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      document.removeEventListener('mousedown', onMouseDown)
    }
  }, [onClose])

  const left = Math.max(8, Math.min(rect.left, window.innerWidth - EST_WIDTH - 8))
  const top = rect.top + EST_HEIGHT > window.innerHeight
    ? Math.max(8, rect.top - EST_HEIGHT - 28)
    : rect.top + 6

  return (
    <div
      ref={ref}
      data-esc-guard
      className="fixed z-40 w-80 rounded-lg border border-slate-700 bg-slate-800 shadow-xl p-3 space-y-2"
      style={{ left, top }}
    >
      <div className="flex items-start gap-2">
        <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${KIND_DOT[issue.kind] || 'bg-slate-400'}`} />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-slate-300">
            {issue.shortMessage || KIND_LABEL[issue.kind] || 'Issue'}
          </p>
          <p className="text-xs text-slate-400 mt-0.5">{issue.message}</p>
        </div>
        <button type="button" className="btn-ghost p-1 -mt-1 -mr-1" onClick={onClose}>
          <X size={13} />
        </button>
      </div>

      {issue.replacements.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {issue.replacements.map((r, i) => (
            <button
              key={i}
              type="button"
              className="btn-secondary text-xs px-2 py-1 font-medium text-emerald-300"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onApply(issue.id, r)}
            >
              {r === '' ? '(delete)' : r}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1 border-t border-slate-700/60">
        <button
          type="button"
          className="btn-ghost text-xs px-1.5 py-1 text-slate-400 hover:text-slate-200"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onDismiss(issue.id)}
        >
          Dismiss
        </button>
        {issue.source === 'lt' && issue.ruleId && onIgnoreRule && (
          <button
            type="button"
            className="btn-ghost text-xs px-1.5 py-1 flex items-center gap-1 text-slate-400 hover:text-slate-200"
            title={`Never show this rule again (${issue.categoryName ? `${issue.categoryName} · ` : ''}${issue.ruleId})`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onIgnoreRule(issue)}
          >
            <EyeOff size={12} /> Ignore rule
          </button>
        )}
        {issue.kind === 'typo' && issue.originalText && !/\s/.test(issue.originalText) && (
          <>
            <div className="flex-1" />
            <button
              type="button"
              className="btn-ghost text-xs px-1.5 py-1 flex items-center gap-1 text-slate-400 hover:text-slate-200"
              title="Add to this book's dictionary"
              disabled={added}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { setAdded(true); onAddToDictionary(issue.originalText) }}
            >
              <BookPlus size={12} /> Add to dictionary
            </button>
            <button
              type="button"
              className="btn-ghost p-1 text-slate-500 hover:text-slate-200"
              title="Add to the global dictionary (all books)"
              disabled={added}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { setAdded(true); onAddToDictionary(issue.originalText, { global: true }) }}
            >
              <Globe size={12} />
            </button>
          </>
        )}
      </div>
    </div>
  )
}
