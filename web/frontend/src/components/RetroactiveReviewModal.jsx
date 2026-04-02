import { useState, useEffect, useCallback } from 'react'
import { X, ChevronLeft, ChevronRight, Save, Sparkles, BookOpen, Copy, Replace, RotateCcw, Loader2, AlertCircle, Trash2 } from 'lucide-react'
import { api } from '../services/api'
import { copyToClipboard } from '../utils/clipboard'
import { DictResult, useDictLookup } from './DictLookup'
import { DEFAULT_CATEGORIES, getCatBadge, catBadgeProps } from '../utils/categories'

export default function RetroactiveReviewModal({ book, onClose }) {
  const [originChapters, setOriginChapters] = useState([])
  const [currentChapter, setCurrentChapter] = useState(null)
  const [rows, setRows] = useState([])
  const [categories, setCategories] = useState(DEFAULT_CATEGORIES)
  const [loading, setLoading] = useState(true)
  const [propagate, setPropagate] = useState(null) // { entityId, oldTranslation, newTranslation, untranslated }
  const [error, setError] = useState(null)

  // Load origin chapters and book categories on mount
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [chapRes, catRes] = await Promise.all([
          api.getOriginChapters(book.id),
          api.getBookCategories(book.id),
        ])
        if (cancelled) return
        setOriginChapters(chapRes.chapters || [])
        if (catRes.categories?.length) setCategories(catRes.categories)
        if (chapRes.chapters?.length) setCurrentChapter(chapRes.chapters[0])
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [book.id])

  // Load entities when chapter changes
  useEffect(() => {
    if (currentChapter == null) return
    let cancelled = false
    setLoading(true)
    ;(async () => {
      try {
        const res = await api.listEntities({ book_id: book.id, origin_chapter: currentChapter })
        if (cancelled) return
        const entityRows = (res.entities || []).map(e => ({
          ...e,
          originalTranslation: e.translation,
          originalCategory: e.category,
          originalGender: e.gender || '',
          originalNote: e.note || '',
          saving: false,
          adviceLoading: false,
          adviceData: null,
        }))
        setRows(entityRows)
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [book.id, currentChapter])

  const update = useCallback((id, patch) =>
    setRows(prev => prev.map(r => r.id === id ? { ...r, ...patch } : r)), [])

  const chapterIdx = originChapters.indexOf(currentChapter)
  const hasPrev = chapterIdx > 0
  const hasNext = chapterIdx < originChapters.length - 1

  const handleSave = async (row) => {
    update(row.id, { saving: true })
    try {
      await api.updateEntity(row.id, {
        translation: row.translation,
        category: row.category,
        gender: row.gender || undefined,
        note: row.note || undefined,
      })
      const translationChanged = row.translation !== row.originalTranslation
      // Update originals so row is no longer dirty
      update(row.id, {
        saving: false,
        originalTranslation: row.translation,
        originalCategory: row.category,
        originalGender: row.gender || '',
        originalNote: row.note || '',
      })
      if (translationChanged) {
        setPropagate({
          entityId: row.id,
          oldTranslation: row.originalTranslation,
          newTranslation: row.translation,
          untranslated: row.untranslated,
        })
      }
    } catch (e) {
      update(row.id, { saving: false })
      setError(e.message)
    }
  }

  const handleAdvice = async (row) => {
    update(row.id, { adviceLoading: true, adviceData: null })
    try {
      const advice = await api.getAdvice({
        untranslated: row.untranslated,
        translation: row.translation,
        category: row.category,
        book_id: book.id,
      })
      update(row.id, { adviceLoading: false, adviceData: advice })
    } catch (e) {
      update(row.id, { adviceLoading: false })
      setError(`Advice failed: ${e.message}`)
    }
  }

  // Arrow key navigation between chapters
  useEffect(() => {
    const handleKey = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return
      if (propagate) return
      if (e.key === 'ArrowLeft' && hasPrev) {
        setCurrentChapter(originChapters[chapterIdx - 1])
      } else if (e.key === 'ArrowRight' && hasNext) {
        setCurrentChapter(originChapters[chapterIdx + 1])
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [chapterIdx, hasPrev, hasNext, originChapters, propagate])

  if (propagate) {
    return (
      <PropagateOverlay
        {...propagate}
        fromChapter={currentChapter}
        onDone={() => setPropagate(null)}
      />
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-4xl bg-slate-800 border border-slate-600 rounded-xl shadow-2xl flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-100">Review Entities</h2>
            <p className="text-xs text-slate-400 mt-0.5">{book.title}</p>
          </div>
          <button className="btn-ghost p-1.5" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        {/* Chapter navigation */}
        <div className="flex items-center gap-3 px-5 py-2.5 border-b border-slate-700 shrink-0">
          <span className="text-xs text-slate-400">Chapter:</span>
          <button
            className="btn-ghost p-1"
            disabled={!hasPrev}
            onClick={() => setCurrentChapter(originChapters[chapterIdx - 1])}
          >
            <ChevronLeft size={14} />
          </button>
          <select
            className="input text-sm py-1 w-32"
            value={currentChapter ?? ''}
            onChange={e => setCurrentChapter(Number(e.target.value))}
          >
            {originChapters.map(ch => (
              <option key={ch} value={ch}>Ch. {ch}</option>
            ))}
          </select>
          <button
            className="btn-ghost p-1"
            disabled={!hasNext}
            onClick={() => setCurrentChapter(originChapters[chapterIdx + 1])}
          >
            <ChevronRight size={14} />
          </button>
          <span className="text-xs text-slate-500 ml-auto">
            {loading ? 'Loading...' : `${rows.length} ${rows.length === 1 ? 'entity' : 'entities'}`}
          </span>
        </div>

        {/* Entity rows */}
        <div className="overflow-y-auto flex-1 px-5 py-3 space-y-2">
          {loading ? (
            <div className="flex items-center justify-center py-12 text-slate-500">
              <Loader2 size={20} className="animate-spin mr-2" /> Loading entities...
            </div>
          ) : rows.length === 0 ? (
            <div className="text-center py-12 text-slate-500 text-sm">
              No entities found for this chapter.
            </div>
          ) : (
            rows.map(row => (
              <EntityRow
                key={row.id}
                row={row}
                categories={categories}
                onUpdate={patch => update(row.id, patch)}
                onSave={() => handleSave(row)}
                onAdvice={() => handleAdvice(row)}
                onDelete={async () => {
                  try {
                    await api.deleteEntity(row.id)
                    setRows(prev => prev.filter(r => r.id !== row.id))
                  } catch (e) {
                    setError(e.message)
                  }
                }}
                bookId={book.id}
              />
            ))
          )}
        </div>

        {error && (
          <div className="px-5 py-2 border-t border-rose-800 bg-rose-950/50 text-rose-400 text-sm shrink-0 flex items-center justify-between">
            <span>{error}</span>
            <button className="text-xs text-rose-300 hover:text-rose-100" onClick={() => setError(null)}>dismiss</button>
          </div>
        )}
      </div>
    </div>
  )
}

function EntityRow({ row, categories, onUpdate, onSave, onAdvice, onDelete, bookId }) {
  const [showAdvice, setShowAdvice] = useState(false)
  const [copied, setCopied] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const { dictQuery, dictData, dictLoading, dictError, lookup: dictLookup, close: dictClose } = useDictLookup()

  const isDirty =
    row.translation !== row.originalTranslation ||
    row.category !== row.originalCategory ||
    (row.gender || '') !== row.originalGender ||
    (row.note || '') !== row.originalNote

  const handleCopyContext = async () => {
    try {
      const res = await api.getEntityContext(row.id)
      const lines = [
        `Entity: ${row.untranslated} -> ${row.translation}`,
        `Category: ${row.category}`,
        '',
        res.context ? `Context:\n${res.context}` : '(no context available)',
      ]
      copyToClipboard(lines.join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="card p-3 space-y-2">
      <div className="flex items-start gap-3">
        {/* Category selector */}
        <select
          {...catBadgeProps(row.category, 'shrink-0 mt-0.5 cursor-pointer appearance-none pr-5 bg-no-repeat bg-[length:12px] bg-[right_4px_center]')}
          style={{ ...getCatBadge(row.category).style, backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")` }}
          value={row.category}
          onChange={e => onUpdate({ category: e.target.value })}
        >
          {categories.map(cat => (
            <option key={cat} value={cat}>{cat}</option>
          ))}
        </select>

        {/* Untranslated */}
        <span className="font-mono text-sm text-slate-200 shrink-0 min-w-[120px]">
          {row.untranslated}
        </span>

        {/* Translation input */}
        <div className="flex-1">
          <input
            className="input text-sm"
            value={row.translation}
            onChange={e => onUpdate({ translation: e.target.value })}
            placeholder="Translation..."
          />
        </div>

        {/* Gender (for characters) */}
        {row.category === 'characters' && (
          <div className="flex shrink-0 gap-0.5">
            {[
              { value: 'male', symbol: '\u2642', color: 'text-blue-400 bg-blue-900/60 border-blue-500' },
              { value: 'female', symbol: '\u2640', color: 'text-pink-400 bg-pink-900/60 border-pink-500' },
              { value: 'neutral', symbol: '\u26B2', color: 'text-slate-300 bg-slate-700/60 border-slate-400' },
            ].map(g => (
              <button
                key={g.value}
                type="button"
                title={g.value}
                className={`w-7 h-7 flex items-center justify-center rounded border text-sm leading-none transition-colors ${
                  row.gender === g.value
                    ? g.color
                    : 'text-slate-500 bg-slate-800/40 border-slate-700 hover:border-slate-500'
                }`}
                onClick={() => onUpdate({ gender: row.gender === g.value ? '' : g.value })}
              >
                {g.symbol}
              </button>
            ))}
          </div>
        )}

        {/* Note */}
        <input
          className="input text-sm w-32 shrink-0"
          value={row.note || ''}
          onChange={e => onUpdate({ note: e.target.value })}
          placeholder="Note..."
          title="Translation guidance for AI"
        />

        {/* Actions */}
        <button
          className="btn-ghost p-1.5 shrink-0"
          title="Copy entity + context"
          onClick={handleCopyContext}
        >
          <Copy size={14} className={copied ? 'text-emerald-400' : 'text-slate-400'} />
        </button>
        <button
          className="btn-ghost p-1.5 shrink-0"
          title="Dictionary lookup"
          onClick={() => dictLookup(row.untranslated)}
        >
          <BookOpen size={14} className={dictQuery ? 'text-indigo-400' : 'text-slate-400'} />
        </button>
        <button
          className="btn-ghost p-1.5 shrink-0"
          title="Ask AI for translation suggestions"
          onClick={() => { onAdvice(); setShowAdvice(true) }}
          disabled={row.adviceLoading}
        >
          <Sparkles size={14} className={row.adviceLoading ? 'animate-pulse text-indigo-400' : 'text-slate-400'} />
        </button>

        {/* Save button - only when dirty */}
        {isDirty && (
          <button
            className="btn-primary p-1.5 shrink-0 flex items-center gap-1"
            title="Save changes"
            onClick={onSave}
            disabled={row.saving}
          >
            {row.saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          </button>
        )}

        {/* Delete button */}
        {confirmDelete ? (
          <div className="flex items-center gap-1 shrink-0">
            <button
              className="text-xs px-1.5 py-0.5 rounded bg-rose-900/60 hover:bg-rose-800 text-rose-200 border border-rose-700"
              onClick={async () => {
                setDeleting(true)
                await onDelete()
                setDeleting(false)
              }}
              disabled={deleting}
            >
              {deleting ? '...' : 'Yes'}
            </button>
            <button
              className="text-xs px-1.5 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 border border-slate-600"
              onClick={() => setConfirmDelete(false)}
              disabled={deleting}
            >
              No
            </button>
          </div>
        ) : (
          <button
            className="btn-ghost p-1.5 shrink-0"
            title="Delete entity"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 size={14} className="text-slate-400 hover:text-rose-400" />
          </button>
        )}
      </div>

      {/* Dictionary result */}
      {dictQuery && (
        <div className="ml-[84px]">
          <DictResult query={dictQuery} data={dictData} loading={dictLoading} error={dictError} onClose={dictClose} />
        </div>
      )}

      {/* AI advice panel */}
      {showAdvice && row.adviceData && (
        <div className="ml-[84px] bg-slate-900/70 rounded p-3 space-y-2">
          <p className="text-xs text-slate-300 leading-relaxed">{row.adviceData.message}</p>
          {row.adviceData.options?.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {row.adviceData.options.map((opt, i) => (
                <button
                  key={i}
                  className="text-xs px-2 py-1 rounded bg-indigo-900/60 hover:bg-indigo-800 text-indigo-200 border border-indigo-700"
                  onClick={() => { onUpdate({ translation: opt }); setShowAdvice(false) }}
                >
                  {opt}
                </button>
              ))}
            </div>
          )}
          <button className="text-xs text-slate-500 hover:text-slate-300" onClick={() => setShowAdvice(false)}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  )
}

function PropagateOverlay({ entityId, oldTranslation, newTranslation, untranslated, fromChapter, onDone }) {
  const [acting, setActing] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const handleAction = async (action) => {
    if (action === 'nothing') { onDone(); return }
    setActing(true); setError(null)
    try {
      const res = await api.propagateChange({
        entity_id: entityId,
        old_translation: oldTranslation,
        new_translation: newTranslation,
        action,
        from_chapter: fromChapter,
      })
      setResult({ action, affected: res.affected })
    } catch (e) {
      setError(e.message)
    } finally {
      setActing(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-lg p-6 space-y-4 shadow-2xl">
        <div className="flex items-center gap-2">
          <AlertCircle size={18} className="text-amber-400 shrink-0" />
          <h2 className="font-semibold text-slate-200">Translation Changed</h2>
        </div>

        <div className="text-sm text-slate-300 space-y-2">
          <p>
            You changed the translation of <span className="font-mono text-slate-200">{untranslated}</span> from{' '}
            <span className="text-rose-400 line-through">{oldTranslation}</span> to{' '}
            <span className="text-emerald-400">{newTranslation}</span>.
          </p>
          <p className="text-slate-400">
            Would you like to update existing chapters from chapter {fromChapter} forward?
          </p>
        </div>

        {!result ? (
          <div className="space-y-2">
            <button
              className="w-full text-left card p-3 hover:bg-slate-700/50 transition-colors flex items-start gap-3"
              onClick={() => handleAction('nothing')}
              disabled={acting}
            >
              <X size={16} className="text-slate-400 mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-medium text-slate-200">Do nothing</p>
                <p className="text-xs text-slate-500">Only update the entity record. Existing chapters are unchanged.</p>
              </div>
            </button>

            <button
              className="w-full text-left card p-3 hover:bg-slate-700/50 transition-colors flex items-start gap-3"
              onClick={() => handleAction('substitute')}
              disabled={acting}
            >
              <Replace size={16} className="text-indigo-400 mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-medium text-slate-200">Find and replace from chapter {fromChapter} forward</p>
                <p className="text-xs text-slate-500">
                  Replace every occurrence of "{oldTranslation}" with "{newTranslation}" in translated chapter text.
                  <span className="text-amber-400"> Use with caution for generic terms.</span>
                </p>
              </div>
            </button>

            <button
              className="w-full text-left card p-3 hover:bg-slate-700/50 transition-colors flex items-start gap-3"
              onClick={() => handleAction('requeue')}
              disabled={acting}
            >
              <RotateCcw size={16} className="text-amber-400 mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-medium text-slate-200">Flag chapters from {fromChapter} forward for retranslation</p>
                <p className="text-xs text-slate-500">
                  Find every chapter from {fromChapter} onward whose original text contains "{untranslated}" and add it to the translation queue.
                </p>
              </div>
            </button>

            {acting && (
              <div className="flex items-center gap-2 text-slate-400 text-sm pt-1">
                <Loader2 size={13} className="animate-spin" /> Working...
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-emerald-400">
              {result.action === 'substitute'
                ? `Replaced text in ${result.affected} chapter${result.affected !== 1 ? 's' : ''}.`
                : `Added ${result.affected} chapter${result.affected !== 1 ? 's' : ''} to the retranslation queue.`}
              {result.affected === 0 && ' No chapters were affected.'}
            </p>
            <div className="flex justify-end">
              <button className="btn-primary" onClick={onDone}>Done</button>
            </div>
          </div>
        )}

        {error && <p className="text-rose-400 text-sm">{error}</p>}
      </div>
    </div>
  )
}
