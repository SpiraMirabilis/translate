/**
 * EntityReviewPanel
 *
 * Appears after translation completes when new entities were found.
 * User can edit translations, delete entities, or accept as-is.
 * On submit, sends edited entity data back to the API.
 */
import { useState } from 'react'
import { CheckCircle, Trash2, Sparkles, ChevronDown, ChevronRight, BookOpen, Copy } from 'lucide-react'
import { api } from '../services/api'
import { copyToClipboard } from '../utils/clipboard'
import { DictResult, useDictLookup } from './DictLookup'
import { DEFAULT_CATEGORIES, getCatBadge, catBadgeProps } from '../utils/categories'

export default function EntityReviewPanel({ entities, context, onDone }) {
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
          result[row.category] = result[row.category] || {}
          result[row.category][row.untranslated] = { deleted: true }
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
          result[row.category][row.untranslated] = entry
        }
      }
      await api.submitReview({ entities: result })
      onDone()
    } catch (e) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  const handleSkip = async () => {
    setSubmitting(true)
    try {
      await api.skipReview()
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
            <h2 className="font-semibold text-slate-100">Entity Review</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              {activeRows.length} new {activeRows.length === 1 ? 'entity' : 'entities'} found — review and edit before saving
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
              Approve & Continue
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

        {/* Entity rows */}
        <div className="overflow-y-auto flex-1 px-5 py-3 space-y-2">
          {activeRows.map(row => (
            <EntityRow
              key={row.id}
              row={row}
              categories={allCategories}
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

function EntityRow({ row, categories, onUpdate, onDelete, onAdvice, onCopyContext, hasContext }) {
  const [showAdvice, setShowAdvice] = useState(false)
  const [copied, setCopied] = useState(false)
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

        {/* Gender (for characters) */}
        {row.category === 'characters' && (
          <div className="flex shrink-0 gap-0.5">
            {[
              { value: 'male', symbol: '♂', color: 'text-blue-400 bg-blue-900/60 border-blue-500' },
              { value: 'female', symbol: '♀', color: 'text-pink-400 bg-pink-900/60 border-pink-500' },
              { value: 'neutral', symbol: '⚲', color: 'text-slate-300 bg-slate-700/60 border-slate-400' },
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
              setCopied(true)
              setTimeout(() => setCopied(false), 1500)
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
