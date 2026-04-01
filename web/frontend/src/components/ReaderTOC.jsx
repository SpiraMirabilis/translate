import { useEffect, useRef } from 'react'
import { X, CheckCircle2 } from 'lucide-react'

export default function ReaderTOC({ open, onClose, book, chapters, currentChapter, onSelect, theme, isPublic }) {
  const activeRef = useRef(null)

  useEffect(() => {
    if (open && activeRef.current) {
      activeRef.current.scrollIntoView({ block: 'center', behavior: 'instant' })
    }
  }, [open, currentChapter])

  if (!open) return null

  const isDark = theme === 'dark'
  const panelBg = isDark ? 'bg-slate-800' : 'bg-white'
  const borderColor = isDark ? 'border-slate-700' : 'border-stone-200'
  const textPrimary = isDark ? 'text-slate-100' : 'text-gray-900'
  const textSecondary = isDark ? 'text-slate-400' : 'text-gray-500'
  const hoverBg = isDark ? 'hover:bg-slate-700' : 'hover:bg-stone-100'
  const activeBg = isDark ? 'bg-indigo-600/20 text-indigo-300' : 'bg-indigo-50 text-indigo-700'

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />

      {/* Panel */}
      <div className={`fixed inset-y-0 left-0 z-50 w-80 max-w-[85vw] ${panelBg} border-r ${borderColor} flex flex-col shadow-2xl`}>
        {/* Header */}
        <div className={`p-4 border-b ${borderColor} flex items-start gap-3`}>
          {book?.cover_image && (
            <img
              src={isPublic ? `/api/public/books/${book.id}/cover/thumb` : `/api/books/${book.id}/cover/thumb`}
              alt=""
              className="w-12 h-[68px] object-cover rounded shadow shrink-0"
            />
          )}
          <div className="flex-1 min-w-0">
            <h2 className={`font-semibold ${textPrimary} truncate`}>{book?.title}</h2>
            {book?.author && <p className={`text-xs ${textSecondary} mt-0.5`}>{book.author}</p>}
            <p className={`text-xs ${textSecondary} mt-0.5`}>{chapters.length} chapters</p>
          </div>
          <button onClick={onClose} className={`${textSecondary} hover:${textPrimary} p-1`}>
            <X size={18} />
          </button>
        </div>

        {/* Chapter list */}
        <div className="flex-1 overflow-y-auto py-2">
          {chapters.map(ch => {
            const isActive = ch.chapter === currentChapter
            return (
              <button
                key={ch.chapter}
                ref={isActive ? activeRef : null}
                onClick={() => { onSelect(ch.chapter); onClose() }}
                className={`w-full text-left px-4 py-2.5 flex items-center gap-2 transition-colors text-sm
                  ${isActive ? activeBg : `${textPrimary} ${hoverBg}`}`}
              >
                <span className={`w-8 text-right shrink-0 text-xs ${isActive ? '' : textSecondary}`}>
                  {ch.chapter}
                </span>
                <span className="flex-1 truncate">{ch.title || `Chapter ${ch.chapter}`}</span>
                {ch.is_proofread ? (
                  <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
                ) : null}
              </button>
            )
          })}
        </div>
      </div>
    </>
  )
}
