import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import ComboBox from '../components/ComboBox'
import {
  Plus, Trash2, Edit2, Download, ChevronDown, ChevronRight,
  BookOpen, FileText, X, Check, Loader2, ScrollText, CheckCircle2, Sparkles, Info
} from 'lucide-react'

export default function Books() {
  const [books, setBooks] = useState([])
  const [loading, setLoading] = useState(true)
  const [expandedBook, setExpandedBook] = useState(null)
  const [chapters, setChapters] = useState({})
  const [showForm, setShowForm] = useState(false)
  const [editingBook, setEditingBook] = useState(null)   // book obj or null
  const [editingChapter, setEditingChapter] = useState(null)
  const [editingPrompt, setEditingPrompt] = useState(null) // book obj or null
  const [retranslating, setRetranslating] = useState(null) // { bookId, chapter, title } or null
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api.listBooks()
      setBooks(d.books || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const loadChapters = async (bookId) => {
    if (chapters[bookId]) return
    const d = await api.listChapters(bookId)
    setChapters(prev => ({ ...prev, [bookId]: d.chapters || [] }))
  }

  const toggleExpand = async (bookId) => {
    if (expandedBook === bookId) {
      setExpandedBook(null)
    } else {
      setExpandedBook(bookId)
      await loadChapters(bookId)
    }
  }

  const handleDelete = async (id) => {
    if (!confirm('Delete this book and all its chapters?')) return
    await api.deleteBook(id)
    load()
  }

  const handleDeleteChapter = async (bookId, num) => {
    if (!confirm(`Delete chapter ${num}?`)) return
    await api.deleteChapter(bookId, num)
    setChapters(prev => ({
      ...prev,
      [bookId]: (prev[bookId] || []).filter(c => c.chapter !== num)
    }))
  }

  const handleExport = async (bookId, format) => {
    try {
      const blob = await api.exportBook(bookId, format)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const extMap = { epub: 'epub', markdown: 'md', html: 'html', text: 'txt' }
      a.download = `book_${bookId}.${extMap[format] || 'txt'}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      alert(`Export failed: ${e.message}`)
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-slate-200">Books</h1>
        <button className="btn-primary flex items-center gap-1.5" onClick={() => { setEditingBook(null); setShowForm(true) }}>
          <Plus size={14} /> New Book
        </button>
      </div>

      {error && <div className="badge-rose mb-4 px-3 py-2 text-sm rounded">{error}</div>}

      {loading ? (
        <div className="flex items-center gap-2 text-slate-400 text-sm">
          <Loader2 size={14} className="animate-spin" /> Loading…
        </div>
      ) : books.length === 0 ? (
        <div className="card p-8 text-center text-slate-500">
          <BookOpen size={32} className="mx-auto mb-3 opacity-40" />
          <p>No books yet. Create one to get started.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {books.map(book => (
            <div key={book.id} className="card">
              {/* Book row */}
              <div className="flex items-center gap-3 p-4">
                <button
                  className="text-slate-500 hover:text-slate-300"
                  onClick={() => toggleExpand(book.id)}
                >
                  {expandedBook === book.id
                    ? <ChevronDown size={16} />
                    : <ChevronRight size={16} />}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-200 truncate">{book.title}</span>
                    <span className="badge-slate text-xs">ID: {book.id}</span>
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    {book.author && <span>{book.author} · </span>}
                    {book.chapter_count ?? 0} chapters · {book.language}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {/* Export */}
                  <div className="relative group">
                    <button className="btn-ghost p-1.5" title="Export">
                      <Download size={14} />
                    </button>
                    <div className="absolute right-0 top-full pt-1 hidden group-hover:flex flex-col z-10 min-w-[120px]">
                    <div className="bg-slate-800 border border-slate-700 rounded shadow-xl flex flex-col">
                      {['text', 'markdown', 'html', 'epub'].map(fmt => (
                        <button
                          key={fmt}
                          className="text-xs text-left px-3 py-1.5 hover:bg-slate-700 text-slate-300"
                          onClick={() => handleExport(book.id, fmt)}
                        >
                          {fmt.toUpperCase()}
                        </button>
                      ))}
                    </div>
                    </div>
                  </div>
                  <button
                    className="btn-ghost p-1.5"
                    title="System Prompt"
                    onClick={() => setEditingPrompt(book)}
                  >
                    <ScrollText size={14} />
                  </button>
                  <button
                    className="btn-ghost p-1.5"
                    title="Edit"
                    onClick={() => { setEditingBook(book); setShowForm(true) }}
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    className="btn-ghost p-1.5 hover:text-rose-400"
                    title="Delete"
                    onClick={() => handleDelete(book.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {/* Chapters */}
              {expandedBook === book.id && (
                <div className="border-t border-slate-700 px-4 py-3">
                  {(chapters[book.id] || []).length === 0 ? (
                    <p className="text-xs text-slate-500">No chapters yet.</p>
                  ) : (
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-xs text-slate-500 border-b border-slate-700">
                          <th className="text-left pb-2 font-medium">Ch.</th>
                          <th className="text-left pb-2 font-medium">Title</th>
                          <th className="text-left pb-2 font-medium">Model</th>
                          <th className="text-left pb-2 font-medium">Date</th>
                          <th className="pb-2" />
                        </tr>
                      </thead>
                      <tbody>
                        {(chapters[book.id] || []).map(ch => (
                          <tr key={ch.chapter} className={`border-b border-slate-800 last:border-0 ${!ch.is_proofread ? 'bg-amber-500/5' : ''}`}>
                            <td className="py-2 text-slate-400 font-mono">
                              <span className="inline-flex items-center gap-1">
                                {ch.chapter}
                                {ch.is_proofread
                                  ? <CheckCircle2 size={11} className="text-emerald-500" />
                                  : <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500/60" title="Not proofread" />
                                }
                              </span>
                            </td>
                            <td className="py-2 text-slate-300 truncate max-w-[240px]">{ch.title}</td>
                            <td className="py-2 text-xs text-slate-500">{ch.model || '—'}</td>
                            <td className="py-2 text-xs text-slate-500">
                              {ch.translation_date ? new Date(ch.translation_date).toLocaleDateString() : '—'}
                            </td>
                            <td className="py-2">
                              <div className="flex gap-1 justify-end">
                                <button
                                  className="btn-ghost p-1"
                                  title="Retranslate chapter"
                                  onClick={() => setRetranslating({ bookId: book.id, chapter: ch.chapter, title: ch.title })}
                                >
                                  <Sparkles size={12} />
                                </button>
                                <Link
                                  to={`/books/${book.id}/chapters/${ch.chapter}/edit`}
                                  className="btn-ghost p-1"
                                  title="Edit translation"
                                >
                                  <Edit2 size={12} />
                                </Link>
                                <button
                                  className="btn-ghost p-1 hover:text-rose-400"
                                  title="Delete chapter"
                                  onClick={() => handleDeleteChapter(book.id, ch.chapter)}
                                >
                                  <Trash2 size={12} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Book form modal */}
      {showForm && (
        <BookFormModal
          book={editingBook}
          onClose={() => setShowForm(false)}
          onSaved={() => { setShowForm(false); load() }}
        />
      )}

      {/* System prompt editor modal */}
      {editingPrompt && (
        <PromptEditorModal
          book={editingPrompt}
          onClose={() => setEditingPrompt(null)}
        />
      )}

      {/* Retranslate modal */}
      {retranslating && (
        <RetranslateModal
          bookId={retranslating.bookId}
          chapterNum={retranslating.chapter}
          chapterTitle={retranslating.title}
          onClose={() => setRetranslating(null)}
        />
      )}
    </div>
  )
}

function BookFormModal({ book, onClose, onSaved }) {
  const [form, setForm] = useState({
    title: book?.title || '',
    author: book?.author || '',
    language: book?.language || 'en',
    description: book?.description || '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleSave = async () => {
    if (!form.title.trim()) { setError('Title is required'); return }
    setSaving(true); setError(null)
    try {
      if (book) {
        await api.updateBook(book.id, form)
      } else {
        await api.createBook(form)
      }
      onSaved()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">{book ? 'Edit Book' : 'New Book'}</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <div className="space-y-3">
          <div><label className="label">Title *</label><input className="input" value={form.title} onChange={e => setForm(f => ({...f, title: e.target.value}))} /></div>
          <div><label className="label">Author</label><input className="input" value={form.author} onChange={e => setForm(f => ({...f, author: e.target.value}))} /></div>
          <div><label className="label">Target Language</label><input className="input" value={form.language} onChange={e => setForm(f => ({...f, language: e.target.value}))} placeholder="en" /></div>
          <div><label className="label">Description</label><textarea className="input h-20 resize-none" value={form.description} onChange={e => setForm(f => ({...f, description: e.target.value}))} /></div>
        </div>

        {error && <p className="text-rose-400 text-sm">{error}</p>}

        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            Save
          </button>
        </div>
      </div>
    </div>
  )
}

function PromptEditorModal({ book, onClose }) {
  const [template, setTemplate] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [hasCustom, setHasCustom] = useState(false)
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)

  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const d = await api.getPrompt(book.id)
        if (d.template) {
          setTemplate(d.template)
          setHasCustom(true)
        } else {
          // Load default template as starting point
          const def = await api.getDefaultPrompt()
          setTemplate(def.template || '')
          setHasCustom(false)
        }
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    })()
  }, [book.id])

  const handleSave = async () => {
    if (!template.includes('{{ENTITIES_JSON}}')) {
      setError('Template must contain the {{ENTITIES_JSON}} placeholder.')
      return
    }
    setSaving(true); setError(null); setSuccessMsg(null)
    try {
      await api.setPrompt(book.id, { template })
      setHasCustom(true)
      setSuccessMsg('Prompt saved.')
      setTimeout(() => setSuccessMsg(null), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    if (!confirm('Reset to the default system prompt? Your custom prompt for this book will be deleted.')) return
    setError(null); setSuccessMsg(null)
    try {
      await api.resetPrompt(book.id)
      const def = await api.getDefaultPrompt()
      setTemplate(def.template || '')
      setHasCustom(false)
      setSuccessMsg('Reset to default.')
      setTimeout(() => setSuccessMsg(null), 3000)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleLoadDefault = async () => {
    setError(null)
    try {
      const def = await api.getDefaultPrompt()
      setTemplate(def.template || '')
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-200">System Prompt — {book.title}</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              {hasCustom
                ? 'This book has a custom system prompt.'
                : 'Using the default system prompt. Save to create a custom one for this book.'}
            </p>
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center p-12 text-slate-400 text-sm">
            <Loader2 size={14} className="animate-spin mr-2" /> Loading…
          </div>
        ) : (
          <>
            <div className="flex-1 overflow-hidden p-4">
              <p className="text-xs text-slate-500 mb-2">
                Use <code className="px-1 py-0.5 bg-slate-700 rounded text-slate-300">{'{{ENTITIES_JSON}}'}</code> where
                the entity list should be inserted at translation time.
              </p>
              <textarea
                className="input w-full h-full min-h-[400px] font-mono text-xs leading-relaxed resize-none"
                value={template}
                onChange={e => setTemplate(e.target.value)}
                spellCheck={false}
              />
            </div>

            {(error || successMsg) && (
              <div className="px-5 shrink-0">
                {error && <p className="text-rose-400 text-sm">{error}</p>}
                {successMsg && <p className="text-emerald-400 text-sm">{successMsg}</p>}
              </div>
            )}

            <div className="flex items-center justify-between px-5 py-3 border-t border-slate-700 shrink-0">
              <div className="flex gap-2">
                <button className="btn-secondary text-xs" onClick={handleLoadDefault}>
                  Load default template
                </button>
                {hasCustom && (
                  <button className="btn-danger text-xs" onClick={handleReset}>
                    Reset to default
                  </button>
                )}
              </div>
              <div className="flex gap-2">
                <button className="btn-secondary" onClick={onClose}>Cancel</button>
                <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                  Save
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function RetranslateModal({ bookId, chapterNum, chapterTitle, onClose }) {
  const [providers, setProviders] = useState([])
  const [translationModel, setTranslationModel] = useLocalStorage('queue.translationModel', '')
  const [adviceModel, setAdviceModel]           = useLocalStorage('shared.adviceModel', '')
  const [cleaningModel, setCleaningModel]       = useLocalStorage('shared.cleaningModel', '')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    api.listProviders().then(d => setProviders(d.providers || [])).catch(() => {})
  }, [])

  const modelOptions = providers.flatMap(p =>
    (p.models || []).map(m => `${p.name}:${m}`)
  )

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      // Fetch the chapter's original Chinese text
      const chapter = await api.getChapter(bookId, chapterNum)
      const untranslated = chapter.untranslated || []
      if (!untranslated.length) {
        setError('No source text found for this chapter.')
        setSubmitting(false)
        return
      }
      // Add to queue
      await api.addToQueue({
        text: untranslated.join('\n'),
        book_id: bookId,
        chapter_number: chapterNum,
        title: chapterTitle,
        priority: true,
      })
      setDone(true)
    } catch (e) {
      setError(e.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">Retranslate Chapter</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <p className="text-sm text-slate-400">
          Queue <span className="text-slate-200">Ch. {chapterNum}</span>
          {chapterTitle && <> — <span className="text-slate-300">{chapterTitle}</span></>}
          {' '}for retranslation. The existing translation will be overwritten when the queue item is processed.
        </p>

        {!done ? (
          <>
            <div className="space-y-3">
              <div>
                <label className="label">Translation model</label>
                <ComboBox
                  value={translationModel}
                  onChange={setTranslationModel}
                  options={modelOptions}
                  placeholder="Default"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label flex items-center gap-1">
                    Advice model
                    <span className="relative group">
                      <Info size={11} className="text-slate-500 hover:text-slate-300 cursor-help" />
                      <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-56 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                        Suggests translations for new entity names. A small, cheap model works well here.
                      </span>
                    </span>
                  </label>
                  <ComboBox
                    value={adviceModel}
                    onChange={setAdviceModel}
                    options={modelOptions}
                    placeholder="Default"
                  />
                </div>
                <div>
                  <label className="label flex items-center gap-1">
                    Cleaning model
                    <span className="relative group">
                      <Info size={11} className="text-slate-500 hover:text-slate-300 cursor-help" />
                      <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-56 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                        Filters out common words misidentified as entities. A small, cheap model works well.
                      </span>
                    </span>
                  </label>
                  <ComboBox
                    value={cleaningModel}
                    onChange={setCleaningModel}
                    options={modelOptions}
                    placeholder="Same as translation"
                  />
                </div>
              </div>
            </div>

            {error && <p className="text-rose-400 text-sm">{error}</p>}

            <div className="flex justify-end gap-2">
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-1.5"
                onClick={handleSubmit}
                disabled={submitting}
              >
                {submitting ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                Queue for Retranslation
              </button>
            </div>
          </>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-emerald-400">
              Chapter {chapterNum} has been added to the translation queue. Go to the Queue page to process it.
            </p>
            <div className="flex justify-end">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
