import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, Loader2, Sparkles, History } from 'lucide-react'
import { getCatBadge } from '../utils/categories'

const GENDER_LABEL = { male: 'male', female: 'female', unknown: 'unknown' }

/**
 * "Terms this chapter" — the book's glossary narrowed to the entities that
 * actually occur in the chapter being read.
 *
 * Terms come from the server pre-indexed (chapter_entities), so opening this is
 * one small request rather than a scan of the book's whole glossary. Notes are
 * the notes as they read AT this chapter, so nothing here spoils a later one.
 */
export default function ReaderTerms({ open, onClose, bookId, chapterNumber, theme,
                                      api, scope, canEdit = false, onEditEntity }) {
  const [filter, setFilter] = useState('')

  const isDark = theme === 'dark'
  const panelBg = isDark ? 'bg-slate-800' : 'bg-white'
  const borderColor = isDark ? 'border-slate-700' : 'border-stone-200'
  const textPrimary = isDark ? 'text-slate-100' : 'text-gray-900'
  const textSecondary = isDark ? 'text-slate-400' : 'text-gray-500'
  const textMuted = isDark ? 'text-slate-500' : 'text-gray-400'
  const hoverBg = isDark ? 'hover:bg-slate-700/60' : 'hover:bg-stone-100'
  const inputBg = isDark ? 'bg-slate-900 border-slate-600 text-slate-100 placeholder-slate-500'
    : 'bg-white border-stone-300 text-gray-900 placeholder-gray-400'

  const termsQuery = useQuery({
    queryKey: [scope, 'chapter-terms', bookId, chapterNumber],
    queryFn: () => api.getChapterTerms(bookId, chapterNumber),
    enabled: open && bookId != null && chapterNumber != null,
    staleTime: 5 * 60 * 1000,
  })
  const terms = useMemo(() => termsQuery.data?.terms || [], [termsQuery.data])

  // Grouped in the order the server sent (the book's own category order).
  const groups = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const matched = q
      ? terms.filter(t =>
          (t.translation || '').toLowerCase().includes(q) ||
          (t.untranslated || '').includes(filter.trim()) ||
          (t.note || '').toLowerCase().includes(q) ||
          (t.category || '').toLowerCase().includes(q))
      : terms
    const out = []
    for (const t of matched) {
      const last = out[out.length - 1]
      if (last && last.category === t.category) last.items.push(t)
      else out.push({ category: t.category, items: [t] })
    }
    return out
  }, [terms, filter])

  const shownCount = groups.reduce((n, g) => n + g.items.length, 0)

  if (!open) return null

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Terms in this chapter"
        className={`fixed top-16 left-1/2 -translate-x-1/2 z-50 w-[560px] max-w-[92vw] ${panelBg}
          border ${borderColor} rounded-xl shadow-2xl flex flex-col max-h-[76vh]`}
      >
        {/* Header */}
        <div className={`p-3 pl-4 border-b ${borderColor} flex items-center gap-3`}>
          <div className="flex-1 min-w-0">
            <h2 className={`text-sm font-semibold ${textPrimary}`}>Terms in this chapter</h2>
            <p className={`text-xs ${textSecondary} mt-0.5`}>
              {termsQuery.isPending
                ? 'Loading…'
                : `${terms.length} term${terms.length === 1 ? '' : 's'}`
                  + (filter.trim() && shownCount !== terms.length ? ` · ${shownCount} shown` : '')
                  + ` · chapter ${chapterNumber}`}
            </p>
          </div>
          <button onClick={onClose} className={`${textSecondary} p-1`} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        {/* Filter — only worth the space once the list is long enough to scan */}
        {terms.length > 12 && (
          <div className={`px-3 py-2 border-b ${borderColor}`}>
            <input
              type="text"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="Filter terms…"
              className={`w-full text-sm px-2 py-1.5 rounded border outline-none focus:ring-1 focus:ring-indigo-500 ${inputBg}`}
            />
          </div>
        )}

        {/* List */}
        <div className="overflow-y-auto flex-1">
          {termsQuery.isPending ? (
            <div className="flex justify-center py-12">
              <Loader2 size={22} className="animate-spin text-indigo-400" />
            </div>
          ) : termsQuery.error ? (
            <p className={`px-4 py-10 text-sm text-center ${textSecondary}`}>
              Couldn’t load the glossary for this chapter.
            </p>
          ) : terms.length === 0 ? (
            <p className={`px-4 py-10 text-sm text-center ${textSecondary}`}>
              No glossary terms recorded for this chapter.
            </p>
          ) : shownCount === 0 ? (
            <p className={`px-4 py-10 text-sm text-center ${textSecondary}`}>
              No term matches “{filter.trim()}”.
            </p>
          ) : groups.map(group => (
            <div key={group.category}>
              <div className={`sticky top-0 px-4 py-1.5 ${panelBg} border-b ${borderColor}`}>
                {(() => {
                  const badge = getCatBadge(group.category)
                  return (
                    <span className={badge.className} style={badge.style}>
                      {group.category.replace(/_/g, ' ')}
                    </span>
                  )
                })()}
              </div>
              {group.items.map(term => {
                const clickable = canEdit && onEditEntity
                return (
                  <div
                    key={term.id}
                    onClick={clickable ? () => onEditEntity(term.id) : undefined}
                    className={`px-4 py-2.5 border-b ${borderColor} ${clickable ? `cursor-pointer ${hoverBg}` : ''}`}
                  >
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className={`text-sm font-medium ${textPrimary}`}>{term.translation}</span>
                      <span className={`text-xs font-mono ${textSecondary}`}>{term.untranslated}</span>
                      {term.gender && (
                        <span className={`text-[11px] ${textMuted}`}>
                          {GENDER_LABEL[term.gender] || term.gender}
                        </span>
                      )}
                      {term.first_seen && (
                        <span className="inline-flex items-center gap-0.5 text-[11px] text-indigo-400"
                              title="First appears in this chapter">
                          <Sparkles size={11} /> new
                        </span>
                      )}
                      {/* Not shown together with "new": a brand-new entity's
                          first note is not an update to anything. */}
                      {term.note_changed && !term.first_seen && (
                        <span className="inline-flex items-center gap-0.5 text-[11px] text-amber-500"
                              title="This term's note changed in this chapter">
                          <History size={11} /> note updated
                        </span>
                      )}
                      {term.occurrences > 1 && (
                        <span className={`ml-auto text-[11px] ${textMuted}`}>×{term.occurrences}</span>
                      )}
                    </div>
                    {term.note && (
                      <p className={`mt-1 text-xs leading-snug ${textSecondary}`}>{term.note}</p>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
