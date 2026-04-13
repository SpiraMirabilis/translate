import { useState, useEffect } from 'react'
import { api } from '../services/api'
import { DEFAULT_CATEGORIES } from '../utils/categories'
import { DictResult, useDictLookup } from './DictLookup'
import { copyToClipboard } from '../utils/clipboard'
import DeleteEntityModal from './DeleteEntityModal'
import {
  X, Check, Loader2, Sparkles, BookOpen, Copy, Replace, RotateCcw, AlertCircle, Trash2
} from 'lucide-react'

function PropagateModal({ entityId, oldTranslation, newTranslation, oldGender, newGender, untranslated, onDone }) {
  const [acting, setActing] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const translationChanged = !!newTranslation && oldTranslation !== newTranslation
  const genderChanged = (oldGender || '') !== (newGender || '')

  const handleAction = async (action) => {
    if (action === 'nothing') { onDone(); return }
    setActing(true); setError(null)
    try {
      const res = await api.propagateChange({
        entity_id: entityId,
        old_translation: oldTranslation,
        new_translation: newTranslation,
        old_gender: oldGender || null,
        new_gender: newGender || null,
        action,
      })
      setResult({
        action,
        affected: res.affected,
      })
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
          <h2 className="font-semibold text-slate-200">
            {translationChanged && genderChanged
              ? 'Translation & Gender Changed'
              : translationChanged
                ? 'Translation Changed'
                : 'Gender Changed'}
          </h2>
        </div>

        <div className="text-sm text-slate-300 space-y-2">
          {translationChanged && (
            <p>
              You changed the translation of <span className="font-mono text-slate-200">{untranslated}</span> from{' '}
              <span className="text-rose-400 line-through">{oldTranslation}</span> to{' '}
              <span className="text-emerald-400">{newTranslation}</span>.
            </p>
          )}
          {genderChanged && (
            <p>
              You changed the gender of <span className="font-mono text-slate-200">{untranslated}</span> from{' '}
              <span className="text-rose-400 line-through">{oldGender || 'unspecified'}</span> to{' '}
              <span className="text-emerald-400">{newGender || 'unspecified'}</span>.
            </p>
          )}
          <p className="text-slate-400">
            Would you like to update existing chapters? This will affect all chapters in this book.
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

            {translationChanged && (
              <button
                className="w-full text-left card p-3 hover:bg-slate-700/50 transition-colors flex items-start gap-3"
                onClick={() => handleAction('substitute')}
                disabled={acting}
              >
                <Replace size={16} className="text-indigo-400 mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-slate-200">Find and replace in all chapters</p>
                  <p className="text-xs text-slate-500">
                    Replace every occurrence of &ldquo;{oldTranslation}&rdquo; with &ldquo;{newTranslation}&rdquo; in translated chapter text.
                    <span className="text-amber-400"> Use with caution for generic terms — may cause unintended replacements.</span>
                  </p>
                </div>
              </button>
            )}

            <button
              className="w-full text-left card p-3 hover:bg-slate-700/50 transition-colors flex items-start gap-3"
              onClick={() => handleAction('requeue')}
              disabled={acting}
            >
              <RotateCcw size={16} className="text-amber-400 mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-medium text-slate-200">Flag chapters for retranslation</p>
                <p className="text-xs text-slate-500">
                  Find every chapter whose original Chinese text contains &ldquo;{untranslated}&rdquo; and add it back to the translation queue.
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

export default function EntityFormModal({ entity, books = [], categories: parentCategories = DEFAULT_CATEGORIES, onClose, onSaved, onDelete }) {
  const [form, setForm] = useState({
    category: entity?.category || 'characters',
    untranslated: entity?.untranslated || '',
    translation: entity?.translation || '',
    gender: entity?.gender || '',
    note: entity?.note || '',
    book_id: entity?.book_id ?? '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  // Fetch categories based on the entity/form's book_id, not the page filter
  const [modalCategories, setModalCategories] = useState(parentCategories)
  useEffect(() => {
    const bookId = form.book_id !== '' ? parseInt(form.book_id) : null
    if (bookId) {
      api.getBookCategories(bookId)
        .then(d => setModalCategories(d.categories || DEFAULT_CATEGORIES))
        .catch(() => setModalCategories(DEFAULT_CATEGORIES))
    } else {
      setModalCategories(DEFAULT_CATEGORIES)
    }
  }, [form.book_id])
  // After saving an edit with a changed translation, show propagation options
  const [propagate, setPropagate] = useState(null) // { entityId, oldTranslation, newTranslation }
  // LLM advice
  const [adviceLoading, setAdviceLoading] = useState(false)
  const [adviceData, setAdviceData] = useState(null)
  // Copy context — pre-fetch on modal open so the copy is synchronous (iOS requires user-gesture)
  const [contextCopied, setContextCopied] = useState(false)
  const [contextLoading, setContextLoading] = useState(true)
  const [contextText, setContextText] = useState(null)
  // Delete confirmation
  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (!entity?.id) { setContextLoading(false); return }
    setContextLoading(true)
    api.getEntityContext(entity.id)
      .then(res => {
        const lines = [
          `Entity: ${entity.untranslated} → ${entity.translation}`,
          `Category: ${entity.category}`,
          '',
        ]
        if (res.context) {
          lines.push(`Context:\n${res.context}`)
        } else {
          lines.push(res.message || 'No context available.')
        }
        setContextText(lines.join('\n'))
      })
      .catch(() => setContextText(null))
      .finally(() => setContextLoading(false))
  }, [entity])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const handleCopyContext = () => {
    if (!contextText) return
    // Build text with current form values (may have been edited)
    const lines = [
      `Entity: ${form.untranslated} → ${form.translation}`,
      `Category: ${form.category}`,
      '',
    ]
    // Append the pre-fetched context portion (everything after the header)
    const ctxPart = contextText.split('\n').slice(3).join('\n')
    lines.push(ctxPart)
    copyToClipboard(lines.join('\n'))
      .then(() => {
        setContextCopied(true)
        setTimeout(() => setContextCopied(false), 1500)
      })
      .catch(e => setError(`Copy failed: ${e.message}`))
  }

  // Dictionary lookup
  const { dictQuery, dictData, dictLoading, dictError, lookup: dictLookup, close: dictClose } = useDictLookup()

  const handleSave = async () => {
    if (!form.untranslated.trim() || !form.translation.trim()) {
      setError('Untranslated and translation are required'); return
    }
    setSaving(true); setError(null)
    try {
      const body = {
        ...form,
        book_id: form.book_id !== '' ? parseInt(form.book_id) : null,
        gender: form.gender || null,
        note: form.note || null,
      }
      if (entity?.id) {
        await api.updateEntity(entity.id, body)
        // If translation or gender changed and entity belongs to a book, offer propagation
        const translationChanged = entity.translation && form.translation !== entity.translation
        const oldGender = (entity.gender || '').toLowerCase()
        const newGender = (form.gender || '').toLowerCase()
        const genderChanged = oldGender !== newGender
        const hasBook = entity.book_id != null
        if ((translationChanged || genderChanged) && hasBook) {
          setSaving(false)
          setPropagate({
            entityId: entity.id,
            oldTranslation: entity.translation,
            newTranslation: form.translation,
            oldGender: entity.gender || '',
            newGender: form.gender || '',
          })
          return
        }
      } else {
        await api.createEntity(body)
      }
      onSaved()
    } catch (e) {
      setError(e.message); setSaving(false)
    }
  }

  const handleAdvice = async () => {
    if (!form.untranslated.trim()) return
    setAdviceLoading(true)
    setAdviceData(null)
    try {
      const advice = await api.getAdvice({
        untranslated: form.untranslated,
        translation: form.translation,
        category: form.category,
      })
      setAdviceData(advice)
    } catch (e) {
      setError(`Advice failed: ${e.message}`)
    } finally {
      setAdviceLoading(false)
    }
  }

  const handleDelete = async (decase) => {
    setShowDeleteModal(false)
    setDeleting(true); setError(null)
    try {
      if (decase && entity.book_id && entity.translation && /^[A-Z]/.test(entity.translation)) {
        await api.decaseEntity({ translation: entity.translation, book_id: entity.book_id })
      }
      await api.deleteEntity(entity.id)
      if (onDelete) onDelete()
      else onSaved()
    } catch (e) {
      setError(e.message); setDeleting(false)
    }
  }

  // If showing propagation options, render that instead of the form
  if (propagate) {
    return (
      <PropagateModal
        {...propagate}
        untranslated={form.untranslated}
        onDone={() => { setPropagate(null); onSaved() }}
      />
    )
  }

  const showBooks = books.length > 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">{entity?.id ? 'Edit Entity' : 'Add Entity'}</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="label">Category</label>
            <select className="input" value={form.category} onChange={e => setForm(f => ({...f, category: e.target.value}))}>
              {modalCategories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Untranslated (Chinese)</label>
            <div className="flex gap-2">
              <input className="input font-mono flex-1" value={form.untranslated} onChange={e => setForm(f => ({...f, untranslated: e.target.value}))} disabled={!!entity?.id} />
              {entity?.id && (
                <button
                  className="btn-ghost p-2 shrink-0"
                  title="Copy entity + context to clipboard"
                  onClick={handleCopyContext}
                  disabled={contextLoading || !contextText}
                >
                  <Copy size={14} className={contextCopied ? 'text-emerald-400' : contextLoading ? 'animate-pulse text-indigo-400' : !contextText ? 'text-slate-600' : 'text-slate-400'} />
                </button>
              )}
              <button
                className="btn-ghost p-2 shrink-0"
                title="Dictionary lookup"
                onClick={() => dictLookup(form.untranslated)}
                disabled={!form.untranslated.trim()}
              >
                <BookOpen size={14} className={dictQuery ? 'text-indigo-400' : 'text-slate-400'} />
              </button>
            </div>
            {dictQuery && (
              <div className="mt-2">
                <DictResult query={dictQuery} data={dictData} loading={dictLoading} error={dictError} onClose={dictClose} />
              </div>
            )}
          </div>
          <div>
            <label className="label">Translation</label>
            <div className="flex gap-2">
              <input className="input flex-1" value={form.translation} onChange={e => setForm(f => ({...f, translation: e.target.value}))} />
              <button
                className="btn-ghost p-2 shrink-0"
                title="Ask AI for translation suggestions"
                onClick={handleAdvice}
                disabled={adviceLoading || !form.untranslated.trim()}
              >
                <Sparkles size={14} className={adviceLoading ? 'animate-pulse text-indigo-400' : 'text-slate-400'} />
              </button>
            </div>
            {adviceData && (
              <div className="mt-2 bg-slate-900/70 rounded p-3 space-y-2">
                <p className="text-xs text-slate-300 leading-relaxed">{adviceData.message}</p>
                {adviceData.options?.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {adviceData.options.map((opt, i) => (
                      <button
                        key={i}
                        className="text-xs px-2 py-1 rounded bg-indigo-900/60 hover:bg-indigo-800 text-indigo-200 border border-indigo-700"
                        onClick={() => { setForm(f => ({...f, translation: opt})); setAdviceData(null) }}
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                )}
                <button className="text-xs text-slate-500 hover:text-slate-300" onClick={() => setAdviceData(null)}>
                  Dismiss
                </button>
              </div>
            )}
          </div>
          {form.category === 'characters' && (
            <div><label className="label">Gender</label>
              <div className="flex gap-1 mt-1">
                {[
                  { value: 'male', symbol: '\u2642', label: 'Male', color: 'text-blue-400 bg-blue-900/60 border-blue-500' },
                  { value: 'female', symbol: '\u2640', label: 'Female', color: 'text-pink-400 bg-pink-900/60 border-pink-500' },
                  { value: 'neutral', symbol: '\u26B2', label: 'Neutral', color: 'text-slate-300 bg-slate-700/60 border-slate-400' },
                ].map(g => (
                  <button
                    key={g.value}
                    type="button"
                    title={g.label}
                    className={`px-3 h-8 flex items-center justify-center gap-1.5 rounded border text-sm transition-colors ${
                      form.gender === g.value
                        ? g.color
                        : 'text-slate-500 bg-slate-800/40 border-slate-700 hover:border-slate-500'
                    }`}
                    onClick={() => setForm(f => ({ ...f, gender: f.gender === g.value ? '' : g.value }))}
                  >
                    <span>{g.symbol}</span>
                    <span className="text-xs">{g.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div><label className="label">Note <span className="text-slate-500 font-normal">(translation guidance for AI)</span></label>
            <input className="input" value={form.note} onChange={e => setForm(f => ({...f, note: e.target.value}))} placeholder="e.g. Use female pronouns in narration" />
          </div>
          {showBooks && (
            <div><label className="label">Book (optional)</label>
              <select className="input" value={form.book_id} onChange={e => setForm(f => ({...f, book_id: e.target.value}))}>
                <option value="">Global (all books)</option>
                {books.map(b => <option key={b.id} value={b.id}>{b.id}: {b.title}</option>)}
              </select>
            </div>
          )}
          {!showBooks && entity?.book_id && (
            <div className="text-xs text-slate-500">Book-specific (book {entity.book_id})</div>
          )}
          {entity?.origin_chapter && (
            <div><label className="label">Origin chapter</label>
              <p className="text-sm text-slate-400 py-1.5">{entity.origin_chapter}</p>
            </div>
          )}
        </div>

        {error && <p className="text-rose-400 text-sm">{error}</p>}

        <div className="flex items-center justify-between">
          {entity?.id ? (
            <button
              className="text-xs text-rose-400/70 hover:text-rose-400 flex items-center gap-1"
              onClick={() => setShowDeleteModal(true)}
              disabled={deleting}
            >
              {deleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              Delete
            </button>
          ) : <div />}
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={onClose}>Cancel</button>
            <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
              Save
            </button>
          </div>
        </div>
      </div>
      {showDeleteModal && (
        <DeleteEntityModal
          entities={[entity]}
          onConfirm={handleDelete}
          onCancel={() => setShowDeleteModal(false)}
        />
      )}
    </div>
  )
}
