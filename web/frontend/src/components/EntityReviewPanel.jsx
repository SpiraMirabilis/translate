/**
 * EntityReviewPanel
 *
 * Appears after translation completes when new entities were found.
 * User can edit translations, delete entities, or accept as-is.
 * On submit, sends edited entity data back to the API.
 */
import { useState } from 'react'
import { CheckCircle, Trash2, Sparkles, ChevronDown, ChevronRight, BookOpen, Copy, StickyNote, AlertTriangle, Undo2 } from 'lucide-react'
import { api } from '../services/api'
import { copyToClipboard } from '../utils/clipboard'
import { useTransientFlag } from '../hooks/useTransientFlag'
import { DictResult, useDictLookup } from './DictLookup'
import { DEFAULT_CATEGORIES, getCatBadge, catBadgeProps } from '../utils/categories'

export default function EntityReviewPanel({ entities, context, onDone, phase = 'post', genderedCategories, bookId = null, noteUpdates = [] }) {
  // Categories that carry a gender attribute for this book (from the review payload).
  // Falls back to the legacy "characters" default when the backend didn't supply a list.
  const genderedSet = (genderedCategories && genderedCategories.length)
    ? genderedCategories
    : ['characters']
  // Build the full list of categories available in this review
  const allCategories = (() => {
    const cats = [...DEFAULT_CATEGORIES]
    for (const cat of Object.keys(entities)) {
      if (!cats.includes(cat)) cats.push(cat)
    }
    return cats
  })()

  // Flatten entities into editable rows
  const initialRows = () => {
    const rows = []
    for (const cat of allCategories) {
      const catEntities = entities[cat] || {}
      for (const [untranslated, data] of Object.entries(catEntities)) {
        rows.push({
          id: `${cat}::${untranslated}`,
          category: cat,
          originalCategory: cat,
          untranslated,
          translation: data.translation || '',
          originalTranslation: data.translation || '',
          gender: data.gender || '',
          note: data.note || '',
          deleted: false,
          adviceLoading: false,
          adviceData: null,
        })
      }
    }
    return rows
  }

  const [rows, setRows] = useState(initialRows)
  // Proposed revisions to notes on entities the book already knows. Accepted by
  // default (Approve takes what's on screen, same as entity rows); the note text
  // is editable, and rejecting leaves the existing note untouched.
  const [noteRows, setNoteRows] = useState(() => (noteUpdates || []).map((u, i) => ({
    key: String(u.entity_id ?? u.untranslated ?? i),
    entityId: u.entity_id ?? null,
    untranslated: u.untranslated,
    translation: u.translation || '',
    oldNote: u.old_note || '',
    note: u.new_note || '',
    originalNote: u.new_note || '',
    // A proposed gender correction on the same entry. hasGender records that the
    // model actually proposed one, so clearing the picker reads as "decline the
    // gender half" rather than as a row that never had one.
    hasGender: !!u.new_gender,
    oldGender: u.old_gender || '',
    gender: u.new_gender || '',
    reason: u.reason || '',
    shrink: !!u.shrink,
    rejected: false,
  })))
  const [showContext, setShowContext] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const update = (id, patch) =>
    setRows(prev => prev.map(r => r.id === id ? { ...r, ...patch } : r))

  const handleAdvice = async (row) => {
    update(row.id, { adviceLoading: true, adviceData: null })
    try {
      const advice = await api.getAdvice({
        untranslated: row.untranslated,
        translation: row.translation,
        category: row.category,
      })
      update(row.id, { adviceLoading: false, adviceData: advice })
    } catch (e) {
      update(row.id, { adviceLoading: false })
      alert(`Advice failed: ${e.message}`)
    }
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      // Rebuild entity structure expected by the backend
      const result = {}
      for (const row of rows) {
        if (row.deleted) {
          const cat = row.originalCategory
          result[cat] = result[cat] || {}
          result[cat][row.untranslated] = { deleted: true }
        } else {
          result[row.category] = result[row.category] || {}
          const entry = {
            translation: row.translation,
            gender: row.gender || undefined,
            note: row.note || undefined,
          }
          // If translation was changed, include the original so the backend
          // can find-and-replace it in the chapter text
          if (row.translation !== row.originalTranslation) {
            entry.incorrect_translation = row.originalTranslation
          }
          // If category was changed, include the original so the backend
          // can move the entity from its old category to the new one
          if (row.category !== row.originalCategory) {
            entry.original_category = row.originalCategory
          }
          result[row.category][row.untranslated] = entry
        }
      }
      // Note decisions travel in their own map, keyed by entity id — the backend
      // reads an omitted map as "leave every note alone".
      const noteResult = {}
      for (const n of noteRows) {
        if (n.rejected) {
          noteResult[n.key] = { rejected: true }
        } else {
          const decision = { note: n.note }
          // Only entries that proposed a gender send one back; null means the
          // reviewer cleared it, which declines that half and keeps the note.
          if (n.hasGender) decision.gender = n.gender || null
          noteResult[n.key] = decision
        }
      }
      await api.submitReview({ entities: result, book_id: bookId, note_updates: noteResult })
      onDone()
    } catch (e) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  const handleSkip = async () => {
    setSubmitting(true)
    try {
      await api.skipReview(bookId)
      onDone()
    } catch (e) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  const handleCopyContext = (row) => {
    if (!context) return
    const idx = context.indexOf(row.untranslated)
    let snippet
    if (idx !== -1) {
      const start = Math.max(0, idx - 100)
      const end = Math.min(context.length, idx + row.untranslated.length + 100)
      snippet = context.slice(start, end)
    }
    const lines = [
      `Entity: ${row.untranslated} → ${row.translation}`,
      `Category: ${row.category}`,
      ...(row.note ? [`Note: ${row.note}`] : []),
      '',
      snippet ? `Context:\n${snippet}` : '(entity not found in chapter text)',
    ]
    copyToClipboard(lines.join('\n'))
  }

  const activeRows = rows.filter(r => !r.deleted)
  const deletedRows = rows.filter(r => r.deleted)

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-4xl bg-slate-800 border border-slate-600 rounded-t-xl shadow-2xl
                      flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-100">
              {phase === 'pre' ? 'Entity Review (Two-pass)' : 'Entity Review'}
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              {`${activeRows.length} new ${activeRows.length === 1 ? 'entity' : 'entities'}`}
              {noteRows.length > 0 && ` and ${noteRows.length} ${noteRows.length === 1 ? 'change' : 'changes'} to known entities`}
              {phase === 'pre'
                ? ' — review and edit before translation begins'
                : ' — review and edit before saving'}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              className="btn-ghost text-xs flex items-center gap-1"
              onClick={() => setShowContext(v => !v)}
            >
              {showContext ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              Context
            </button>
            <button className="btn-secondary" onClick={handleSkip} disabled={submitting}>
              Skip Review
            </button>
            <button className="btn-primary flex items-center gap-1.5" onClick={handleSubmit} disabled={submitting}>
              <CheckCircle size={14} />
              {phase === 'pre' ? 'Approve & Translate' : 'Approve & Continue'}
            </button>
          </div>
        </div>

        {/* Context snippet */}
        {showContext && context && (
          <div className="px-5 py-2 border-b border-slate-700 bg-slate-900/60 shrink-0">
            <p className="text-xs text-slate-500 mb-1">Original text (excerpt)</p>
            <pre className="text-xs text-slate-300 whitespace-pre-wrap font-mono leading-relaxed max-h-28 overflow-y-auto">
              {context.slice(0, 800)}{context.length > 800 ? '…' : ''}
            </pre>
          </div>
        )}

        {/* Proposed note revisions on entities the book already knows */}
        {noteRows.length > 0 && (
          <div className="px-5 py-3 border-b border-slate-700 bg-amber-500/5 shrink-0 max-h-64 overflow-y-auto">
            <p className="text-xs text-amber-300/90 mb-2 flex items-center gap-1.5">
              <StickyNote size={13} />
              Note and gender changes on existing entities ({noteRows.filter(n => !n.rejected).length} of {noteRows.length} accepted)
            </p>
            <div className="space-y-2">
              {noteRows.map(n => (
                <div
                  key={n.key}
                  className={`rounded border border-slate-700 bg-slate-900/60 px-3 py-2 ${n.rejected ? 'opacity-40' : ''}`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-mono text-slate-200">{n.untranslated}</span>
                    {n.translation && <span className="text-xs text-slate-400">→ {n.translation}</span>}
                    {n.shrink && (
                      <span
                        className="text-[11px] text-amber-400 flex items-center gap-1"
                        title="Much shorter than the note it replaces"
                      >
                        <AlertTriangle size={11} /> shorter
                      </span>
                    )}
                    <button
                      className="text-xs text-slate-500 hover:text-slate-300 ml-auto flex items-center gap-1"
                      onClick={() => setNoteRows(prev => prev.map(r =>
                        r.key === n.key ? { ...r, rejected: !r.rejected } : r))}
                    >
                      {n.rejected ? <><Undo2 size={12} /> Keep change</> : <><Trash2 size={12} /> Reject</>}
                    </button>
                  </div>
                  {n.oldNote && (
                    <p className="text-xs text-slate-500 mb-1">
                      <span className="text-slate-600">was:</span> {n.oldNote}
                    </p>
                  )}
                  {(n.note || !n.hasGender) && (
                    <input
                      className="input text-xs w-full"
                      value={n.note}
                      disabled={n.rejected}
                      onChange={e => setNoteRows(prev => prev.map(r =>
                        r.key === n.key ? { ...r, note: e.target.value } : r))}
                    />
                  )}
                  {n.hasGender && (
                    <div className="flex items-center gap-2 mt-1.5">
                      <span className="text-[11px] text-slate-500">
                        gender: <span className="text-slate-400">{n.oldGender || '(unset)'}</span> →
                      </span>
                      <GenderPicker
                        value={n.gender}
                        disabled={n.rejected}
                        onChange={g => setNoteRows(prev => prev.map(r =>
                          r.key === n.key ? { ...r, gender: g } : r))}
                      />
                      <span className="text-[11px] text-slate-600">
                        clear to keep {n.oldGender || 'it unset'}
                      </span>
                    </div>
                  )}
                  {n.reason && (
                    <p className="text-[11px] text-slate-500 mt-1 italic">{n.reason}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Entity rows */}
        <div className="overflow-y-auto flex-1 px-5 py-3 space-y-2">
          {activeRows.map(row => (
            <EntityRow
              key={row.id}
              row={row}
              categories={allCategories}
              gendered={genderedSet.includes(row.category)}
              onUpdate={patch => update(row.id, patch)}
              onDelete={() => update(row.id, { deleted: true })}
              onAdvice={() => handleAdvice(row)}
              onCopyContext={() => handleCopyContext(row)}
              hasContext={!!context}
            />
          ))}

          {deletedRows.length > 0 && (
            <div className="mt-4">
              <p className="text-xs text-slate-500 mb-2">Marked for deletion ({deletedRows.length})</p>
              {deletedRows.map(row => (
                <div key={row.id} className="flex items-center gap-3 py-1.5 opacity-40">
                  <span {...catBadgeProps(row.category)}>{row.category}</span>
                  <span className="text-sm font-mono line-through text-slate-400">{row.untranslated}</span>
                  <button
                    className="text-xs text-slate-500 hover:text-slate-300 ml-auto"
                    onClick={() => update(row.id, { deleted: false })}
                  >
                    Restore
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div className="px-5 py-2 border-t border-rose-800 bg-rose-950/50 text-rose-400 text-sm shrink-0">
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

function EntityRow({ row, categories, gendered, onUpdate, onDelete, onAdvice, onCopyContext, hasContext }) {
  const [showAdvice, setShowAdvice] = useState(false)
  const [copied, flashCopied] = useTransientFlag(1500)
  const { dictQuery, dictData, dictLoading, dictError, lookup: dictLookup, close: dictClose } = useDictLookup()

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
            placeholder="Translation…"
          />
        </div>

        {/* Gender (for gender-tracked categories) */}
        {gendered && (
          <GenderPicker
            value={row.gender}
            onChange={g => onUpdate({ gender: g })}
          />
        )}

        {/* Note */}
        <input
          className="input text-sm w-32 shrink-0"
          value={row.note}
          onChange={e => onUpdate({ note: e.target.value })}
          placeholder="Note…"
          title="Translation guidance for AI"
        />

        {/* Actions */}
        {hasContext && (
          <button
            className="btn-ghost p-1.5 shrink-0"
            title="Copy entity + context to clipboard"
            onClick={() => {
              onCopyContext()
              flashCopied()
            }}
          >
            <Copy size={14} className={copied ? 'text-emerald-400' : 'text-slate-400'} />
          </button>
        )}
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
        <button
          className="btn-ghost p-1.5 shrink-0 hover:text-rose-400"
          title="Delete entity"
          onClick={onDelete}
        >
          <Trash2 size={14} />
        </button>
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

/**
 * GenderPicker
 *
 * Three-way toggle used both on a new entity row and on a proposed correction to
 * a known entity. Clicking the active value clears it — on a correction row that
 * is how you decline the gender half while keeping the note.
 */
export function GenderPicker({ value, onChange, disabled = false }) {
  const options = [
    { value: 'male', symbol: '♂', color: 'text-blue-400 bg-blue-900/60 border-blue-500' },
    { value: 'female', symbol: '♀', color: 'text-pink-400 bg-pink-900/60 border-pink-500' },
    { value: 'neutral', symbol: '⚲', color: 'text-slate-300 bg-slate-700/60 border-slate-400' },
  ]
  return (
    <div className="flex shrink-0 gap-0.5">
      {options.map(g => (
        <button
          key={g.value}
          type="button"
          title={g.value}
          disabled={disabled}
          className={`w-7 h-7 flex items-center justify-center rounded border text-sm leading-none transition-colors ${
            value === g.value
              ? g.color
              : 'text-slate-500 bg-slate-800/40 border-slate-700 hover:border-slate-500'
          } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
          onClick={() => onChange(value === g.value ? '' : g.value)}
        >
          {g.symbol}
        </button>
      ))}
    </div>
  )
}
