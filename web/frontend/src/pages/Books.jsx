import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { useWs } from '../App'
import { useLocalStorage } from '../hooks/useLocalStorage'
import ComboBox from '../components/ComboBox'
import {
  Plus, Trash2, Edit2, Download, ChevronDown, ChevronRight,
  BookOpen, FileText, X, Check, Loader2, ScrollText, CheckCircle2, Sparkles, Info, Globe, Tags, Search, Eye, EyeOff, ListChecks
} from 'lucide-react'
import { DEFAULT_CATEGORIES, catBadgeProps } from '../utils/categories'
import GlobalSearchModal from '../components/GlobalSearchModal'
import RetroactiveReviewModal from '../components/RetroactiveReviewModal'

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
  const [publishingBook, setPublishingBook] = useState(null) // book obj or null
  const [categoriesBook, setCategoriesBook] = useState(null) // book obj or null
  const [showSearch, setShowSearch] = useState(false)
  const [reviewingBook, setReviewingBook] = useState(null)
  const [exporting, setExporting] = useState(null) // 'bookId-format' or null
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

  // Ctrl+F opens global search
  useEffect(() => {
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault()
        setShowSearch(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

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
    setExporting(`${bookId}-${format}`)
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
    } finally {
      setExporting(null)
    }
  }

  const togglePublic = async (book) => {
    try {
      await api.updateBook(book.id, { is_public: !book.is_public })
      setBooks(prev => prev.map(b => b.id === book.id ? { ...b, is_public: !book.is_public } : b))
    } catch (e) {
      alert(`Failed to update visibility: ${e.message}`)
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-slate-200">Books</h1>
        <div className="flex items-center gap-2">
          <button
            className="btn-ghost p-2 text-slate-400 hover:text-slate-200"
            onClick={() => setShowSearch(true)}
            title="Search across books (Ctrl+F)"
          >
            <Search size={16} />
          </button>
          <button className="btn-primary flex items-center gap-1.5" onClick={() => { setEditingBook(null); setShowForm(true) }}>
            <Plus size={14} /> New Book
          </button>
        </div>
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
              <div className="flex items-center gap-3 p-3 md:p-4">
                <button
                  className="text-slate-500 hover:text-slate-300"
                  onClick={() => toggleExpand(book.id)}
                >
                  {expandedBook === book.id
                    ? <ChevronDown size={16} />
                    : <ChevronRight size={16} />}
                </button>
                {book.cover_image && (
                  <img
                    src={`/api/books/${book.id}/cover/thumb`}
                    alt=""
                    className="w-8 h-11 object-cover rounded border border-slate-700 shrink-0"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-200 truncate">{book.title}</span>
                    <span className="badge-slate text-xs">ID: {book.id}</span>
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-1 flex-wrap">
                    {book.author && <span>{book.author} · </span>}
                    <span>
                      {book.chapter_count ?? 0} chapters
                      {book.total_source_chapters > 0 && (
                        <> / {book.total_source_chapters} ({Math.round(((book.chapter_count ?? 0) / book.total_source_chapters) * 100)}%)</>
                      )}
                    </span>
                    <span>· {book.language}</span>
                    {book.status && book.status !== 'ongoing' && (
                      <span className={`ml-1 px-1.5 py-0 rounded text-[10px] font-medium ${
                        book.status === 'completed' ? 'bg-emerald-500/20 text-emerald-400' :
                        book.status === 'hiatus' ? 'bg-amber-500/20 text-amber-400' :
                        book.status === 'dropped' ? 'bg-rose-500/20 text-rose-400' :
                        'bg-slate-500/20 text-slate-400'
                      }`}>{book.status}</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {/* Read */}
                  <Link to={`/read/${book.id}`} className="btn-ghost p-1.5" title="Read">
                    <BookOpen size={14} />
                  </Link>
                  {/* Public visibility toggle */}
                  <button
                    className={`btn-ghost p-1.5 ${book.is_public === false ? 'text-rose-400/60' : 'text-emerald-400/60'}`}
                    title={book.is_public === false ? 'Hidden from public library (click to make public)' : 'Visible in public library (click to hide)'}
                    onClick={() => togglePublic(book)}
                  >
                    {book.is_public === false ? <EyeOff size={14} /> : <Eye size={14} className="text-emerald-400" />}
                  </button>
                  {/* Actions dropdown */}
                  <BookActionsMenu
                    book={book}
                    exporting={exporting}
                    onExport={handleExport}
                    onPublish={() => setPublishingBook(book)}
                    onCategories={() => setCategoriesBook(book)}
                    onReview={() => setReviewingBook(book)}
                    onPrompt={() => setEditingPrompt(book)}
                    onEdit={() => { setEditingBook(book); setShowForm(true) }}
                    onDelete={() => handleDelete(book.id)}
                  />
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
                          <th className="text-left pb-2 font-medium hidden sm:table-cell">Model</th>
                          <th className="text-left pb-2 font-medium hidden sm:table-cell">Date</th>
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
                            <td className="py-2 text-xs text-slate-500 hidden sm:table-cell">{ch.model || '—'}</td>
                            <td className="py-2 text-xs text-slate-500 hidden sm:table-cell">
                              {ch.translation_date ? new Date(ch.translation_date).toLocaleDateString() : '—'}
                            </td>
                            <td className="py-2">
                              <div className="flex gap-1 justify-end">
                                <Link
                                  to={`/read/${book.id}?chapter=${ch.chapter}`}
                                  className="btn-ghost p-1"
                                  title="Read from here"
                                >
                                  <BookOpen size={12} />
                                </Link>
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

      {/* WordPress publish modal */}
      {publishingBook && (
        <WordPressPublishModal
          book={publishingBook}
          onClose={() => setPublishingBook(null)}
        />
      )}

      {/* Category manager modal */}
      {categoriesBook && (
        <CategoryManagerModal
          book={categoriesBook}
          onClose={() => setCategoriesBook(null)}
        />
      )}

      {reviewingBook && (
        <RetroactiveReviewModal
          book={reviewingBook}
          onClose={() => setReviewingBook(null)}
        />
      )}

      {/* Global search modal */}
      {showSearch && (
        <GlobalSearchModal
          books={books}
          onClose={() => setShowSearch(false)}
        />
      )}
    </div>
  )
}

function BookActionsMenu({ book, exporting, onExport, onPublish, onCategories, onReview, onPrompt, onEdit, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handleClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  const item = (icon, label, onClick, className = '') => (
    <button
      className={`text-xs text-left px-3 py-1.5 hover:bg-slate-700 text-slate-300 flex items-center gap-2 w-full ${className}`}
      onClick={() => { setOpen(false); onClick() }}
    >
      {icon} {label}
    </button>
  )

  return (
    <div className="relative" ref={ref}>
      <button className="btn-ghost p-1.5 flex items-center gap-0.5 text-xs" onClick={() => setOpen(v => !v)}>
        Actions <ChevronDown size={12} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 min-w-[180px] bg-slate-800 border border-slate-700 rounded shadow-xl flex flex-col py-1">
          {/* Export submenu */}
          <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Export</div>
          {['text', 'markdown', 'html', 'epub'].map(fmt => (
            <button
              key={fmt}
              className="text-xs text-left px-3 py-1.5 hover:bg-slate-700 text-slate-300 flex items-center gap-2 disabled:opacity-50"
              onClick={() => { setOpen(false); onExport(book.id, fmt) }}
              disabled={!!exporting}
            >
              {exporting === `${book.id}-${fmt}` ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
              {fmt.toUpperCase()}
            </button>
          ))}
          <div className="border-t border-slate-700 my-1" />
          <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Publish</div>
          {item(<Globe size={12} />, 'WordPress', onPublish)}
          <div className="border-t border-slate-700 my-1" />
          <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Entities</div>
          {item(<Tags size={12} />, 'Categories', onCategories)}
          {item(<ListChecks size={12} />, 'Review Entities', onReview)}
          <div className="border-t border-slate-700 my-1" />
          <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Settings</div>
          {item(<ScrollText size={12} />, 'System Prompt', onPrompt)}
          {item(<Edit2 size={12} />, 'Edit Book', onEdit)}
          {item(<Trash2 size={12} />, 'Delete', onDelete, 'text-rose-400 hover:text-rose-300')}
        </div>
      )}
    </div>
  )
}

function BookFormModal({ book, onClose, onSaved }) {
  const [form, setForm] = useState({
    title: book?.title || '',
    author: book?.author || '',
    language: book?.language || 'en',
    source_language: book?.source_language || 'zh',
    description: book?.description || '',
    genre: '',
    total_source_chapters: book?.total_source_chapters || '',
    status: book?.status || 'ongoing',
  })
  const [genres, setGenres] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [coverPreview, setCoverPreview] = useState(book?.cover_image ? `/api/books/${book.id}/cover` : null)
  const [uploadingCover, setUploadingCover] = useState(false)
  const fileInputRef = useRef(null)

  // Fetch genres on mount (new books only)
  useEffect(() => {
    if (!book) {
      api.listGenres().then(d => setGenres(d.genres || [])).catch(() => {})
    }
  }, [book])

  const handleGenreChange = (genreId) => {
    setForm(f => ({ ...f, genre: genreId }))
    const genre = genres.find(g => g.id === genreId)
    if (genre && genre.source_language) {
      setForm(f => ({ ...f, genre: genreId, source_language: genre.source_language }))
    }
  }

  const handleSave = async () => {
    if (!form.title.trim()) { setError('Title is required'); return }
    setSaving(true); setError(null)
    try {
      if (book) {
        // Don't send genre or source_language on edit
        const { genre, source_language, ...editForm } = form
        editForm.total_source_chapters = editForm.total_source_chapters ? parseInt(editForm.total_source_chapters, 10) : null
        await api.updateBook(book.id, editForm)
      } else {
        await api.createBook(form)
      }
      onSaved()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  const handleCoverUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file || !book) return
    setUploadingCover(true); setError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      await api.uploadCover(book.id, fd)
      setCoverPreview(`/api/books/${book.id}/cover?t=${Date.now()}`)
    } catch (e) {
      setError(e.message)
    } finally {
      setUploadingCover(false)
    }
  }

  const handleCoverDelete = async () => {
    if (!book) return
    setError(null)
    try {
      await api.deleteCover(book.id)
      setCoverPreview(null)
    } catch (e) {
      setError(e.message)
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
          {/* Genre selector — new books only */}
          {!book && genres.length > 0 && (
            <div>
              <label className="label">Genre Preset</label>
              <select
                className="input"
                value={form.genre}
                onChange={e => handleGenreChange(e.target.value)}
              >
                <option value="">— Select genre —</option>
                {genres.map(g => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
              {form.genre && genres.find(g => g.id === form.genre)?.description && (
                <p className="text-xs text-slate-500 mt-1">{genres.find(g => g.id === form.genre).description}</p>
              )}
            </div>
          )}

          <div><label className="label">Title *</label><input className="input" value={form.title} onChange={e => setForm(f => ({...f, title: e.target.value}))} /></div>
          <div><label className="label">Author</label><input className="input" value={form.author} onChange={e => setForm(f => ({...f, author: e.target.value}))} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Source Language</label><input className="input" value={form.source_language} onChange={e => setForm(f => ({...f, source_language: e.target.value}))} placeholder="zh" /></div>
            <div><label className="label">Target Language</label><input className="input" value={form.language} onChange={e => setForm(f => ({...f, language: e.target.value}))} placeholder="en" /></div>
          </div>
          <div><label className="label">Description</label><textarea className="input h-20 resize-none" value={form.description} onChange={e => setForm(f => ({...f, description: e.target.value}))} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Total Source Chapters</label>
              <input className="input" type="number" min="0" placeholder="Optional" value={form.total_source_chapters} onChange={e => setForm(f => ({...f, total_source_chapters: e.target.value}))} />
            </div>
            <div>
              <label className="label">Status</label>
              <select className="input" value={form.status} onChange={e => setForm(f => ({...f, status: e.target.value}))}>
                <option value="ongoing">Ongoing</option>
                <option value="hiatus">Hiatus</option>
                <option value="completed">Completed</option>
                <option value="dropped">Dropped</option>
              </select>
            </div>
          </div>

          {/* Cover image */}
          {book && (
            <div>
              <label className="label">Cover Image</label>
              <div className="flex items-start gap-3">
                {coverPreview ? (
                  <div className="relative group">
                    <img src={coverPreview} alt="Cover" className="w-20 h-28 object-cover rounded border border-slate-700" />
                    <button
                      className="absolute -top-1.5 -right-1.5 bg-rose-600 rounded-full p-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={handleCoverDelete}
                      title="Remove cover"
                    >
                      <X size={10} className="text-white" />
                    </button>
                  </div>
                ) : (
                  <div className="w-20 h-28 rounded border border-dashed border-slate-600 flex items-center justify-center text-slate-600 text-xs">
                    No cover
                  </div>
                )}
                <div className="flex flex-col gap-1.5">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={handleCoverUpload}
                  />
                  <button
                    className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploadingCover}
                  >
                    {uploadingCover ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
                    {coverPreview ? 'Replace' : 'Upload'}
                  </button>
                </div>
              </div>
            </div>
          )}
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
      <div className="card w-full max-w-4xl max-w-[95vw] max-h-[90vh] flex flex-col shadow-2xl">
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

function WordPressPublishModal({ book, onClose }) {
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState(null)
  const [storyStatus, setStoryStatus] = useState('Ongoing')
  const [storyRating, setStoryRating] = useState('Everyone')
  const [chapterGroup, setChapterGroup] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [progress, setProgress] = useState(null) // { current, total, title }
  const [result, setResult] = useState(null) // { created, updated, skipped, errors }
  const [error, setError] = useState(null)
  const { subscribe } = useWs()

  useEffect(() => {
    api.wpBookStatus(book.id)
      .then(d => setStatus(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [book.id])

  // Listen to WebSocket for publish progress — use subscribe() to guarantee
  // every message is processed, even when messages arrive faster than React renders.
  useEffect(() => {
    return subscribe((m) => {
      if (m.type !== 'wp_publish') return
      if (m.step === 'chapter') {
        setProgress({ current: m.current, total: m.total, title: m.title })
      } else if (m.step === 'done') {
        setResult({ created: m.created, updated: m.updated, skipped: m.skipped, errors: m.errors })
        setPublishing(false)
      } else if (m.step === 'error') {
        setError(m.error)
        setPublishing(false)
      } else if (m.step === 'cancelled') {
        setPublishing(false)
        setError('Publish cancelled.')
      }
    })
  }, [subscribe])

  const handlePublish = async () => {
    setPublishing(true)
    setProgress(null)
    setResult(null)
    setError(null)
    try {
      await api.wpPublish(book.id, {
        story_status: storyStatus,
        story_rating: storyRating,
        chapter_group: chapterGroup,
      })
    } catch (e) {
      setError(e.message)
      setPublishing(false)
    }
  }

  const handleCancel = async () => {
    try { await api.wpCancelPublish(book.id) } catch {}
  }

  const statusBadge = (s) => {
    if (s === 'published') return <span className="badge-emerald text-xs">Published</span>
    if (s === 'changed') return <span className="badge-amber text-xs">Changed</span>
    return <span className="badge-slate text-xs">New</span>
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-200">Publish to WordPress</h2>
            <p className="text-xs text-slate-500 mt-0.5">{book.title}</p>
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center p-12 text-slate-400 text-sm">
            <Loader2 size={14} className="animate-spin mr-2" /> Loading status...
          </div>
        ) : (
          <div className="flex-1 overflow-auto p-5 space-y-4">
            {/* Story info */}
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Globe size={14} />
              {status?.story_published
                ? <span>Story published (WP ID: {status.story_wp_post_id})</span>
                : <span>Story not yet published</span>
              }
            </div>

            {/* Options */}
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="label">Story Status</label>
                <select className="input text-sm" value={storyStatus} onChange={e => setStoryStatus(e.target.value)}>
                  <option>Ongoing</option>
                  <option>Completed</option>
                  <option>Hiatus</option>
                  <option>Canceled</option>
                </select>
              </div>
              <div>
                <label className="label">Rating</label>
                <select className="input text-sm" value={storyRating} onChange={e => setStoryRating(e.target.value)}>
                  <option>Everyone</option>
                  <option>Teen</option>
                  <option>Mature</option>
                  <option>Adult</option>
                </select>
              </div>
              <div>
                <label className="label">Chapter Group</label>
                <input
                  className="input text-sm"
                  value={chapterGroup}
                  onChange={e => setChapterGroup(e.target.value)}
                  placeholder="e.g. Volume 1"
                />
              </div>
            </div>

            {/* Progress — above chapter list so it's always visible */}
            {publishing && progress && (
              <div className="space-y-2">
                <div className="flex justify-between text-xs text-slate-400">
                  <span>Publishing: {progress.title}</span>
                  <span>{progress.current} / {progress.total}</span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-2">
                  <div
                    className="bg-blue-500 h-2 rounded-full transition-all"
                    style={{ width: `${(progress.current / progress.total) * 100}%` }}
                  />
                </div>
              </div>
            )}

            {/* Result */}
            {result && (
              <div className="bg-emerald-950/50 border border-emerald-800 rounded px-3 py-2 text-sm text-emerald-300">
                Done: {result.created} created, {result.updated} updated, {result.skipped} skipped
                {result.errors > 0 && <span className="text-rose-400">, {result.errors} errors</span>}
              </div>
            )}

            {error && <p className="text-rose-400 text-sm">{error}</p>}

            {/* Chapters table */}
            {status?.chapters?.length > 0 && (
              <div className="border border-slate-700 rounded overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-slate-500 bg-slate-800/50">
                      <th className="text-left px-3 py-2 font-medium">Ch.</th>
                      <th className="text-left px-3 py-2 font-medium">Title</th>
                      <th className="text-left px-3 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.chapters.map(ch => (
                      <tr key={ch.chapter_number} className="border-t border-slate-800">
                        <td className="px-3 py-1.5 text-slate-400 font-mono">{ch.chapter_number}</td>
                        <td className="px-3 py-1.5 text-slate-300 truncate max-w-[300px]">{ch.title}</td>
                        <td className="px-3 py-1.5">{statusBadge(ch.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-700 shrink-0">
          <button className="btn-secondary" onClick={onClose}>Close</button>
          {publishing ? (
            <button className="btn-danger flex items-center gap-1.5" onClick={handleCancel}>
              <X size={13} /> Cancel
            </button>
          ) : (
            <button
              className="btn-primary flex items-center gap-1.5"
              onClick={handlePublish}
              disabled={loading || !status?.chapters?.length}
            >
              <Globe size={13} /> Publish All
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function CategoryManagerModal({ book, onClose }) {
  const [categories, setCategories] = useState([])
  const [loading, setLoading] = useState(true)
  const [isDefault, setIsDefault] = useState(true)
  const [newCat, setNewCat] = useState('')
  const [entityCounts, setEntityCounts] = useState({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)

  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const [catData, countData] = await Promise.all([
          api.getBookCategories(book.id),
          api.getCategoryEntityCounts(book.id),
        ])
        setCategories(catData.categories || DEFAULT_CATEGORIES)
        setIsDefault(catData.is_default)
        setEntityCounts(countData.counts || {})
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    })()
  }, [book.id])

  const handleSave = async (cats) => {
    setSaving(true); setError(null); setSuccessMsg(null)
    try {
      const res = await api.setBookCategories(book.id, { categories: cats })
      setCategories(res.categories)
      setIsDefault(false)
      setSuccessMsg('Categories saved.')
      setTimeout(() => setSuccessMsg(null), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleAdd = () => {
    const c = newCat.trim().toLowerCase()
    if (!c) return
    if (categories.includes(c)) { setError(`"${c}" already exists.`); return }
    setError(null)
    const updated = [...categories, c]
    setCategories(updated)
    setNewCat('')
    handleSave(updated)
  }

  const handleRemove = (cat) => {
    const count = entityCounts[cat] || 0
    if (count > 0 && !confirm(`"${cat}" has ${count} entities. They won't be deleted but will be hidden from translation prompts and UI filters. Continue?`)) return
    const updated = categories.filter(c => c !== cat)
    if (updated.length === 0) { setError('At least one category is required.'); return }
    setCategories(updated)
    handleSave(updated)
  }

  const handleReset = async () => {
    if (!confirm('Reset to default categories? Custom categories will be removed (entities are preserved).')) return
    setSaving(true); setError(null); setSuccessMsg(null)
    try {
      await api.resetBookCategories(book.id)
      setCategories([...DEFAULT_CATEGORIES])
      setIsDefault(true)
      setSuccessMsg('Reset to defaults.')
      setTimeout(() => setSuccessMsg(null), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-slate-200">Entity Categories</h2>
            <p className="text-xs text-slate-500 mt-0.5">{book.title}</p>
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Loader2 size={14} className="animate-spin" /> Loading...
          </div>
        ) : (
          <>
            <div className="space-y-1.5">
              {categories.map(cat => (
                <div key={cat} className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-slate-750/50">
                  <div className="flex items-center gap-2">
                    <span {...catBadgeProps(cat)}>{cat}</span>
                    {entityCounts[cat] > 0 && (
                      <span className="text-xs text-slate-500">{entityCounts[cat]} entities</span>
                    )}
                  </div>
                  <button
                    className="btn-ghost p-1 hover:text-rose-400"
                    title="Remove category"
                    onClick={() => handleRemove(cat)}
                    disabled={saving}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>

            <div className="flex gap-2">
              <input
                className="input flex-1"
                placeholder="New category name..."
                value={newCat}
                onChange={e => setNewCat(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAdd()}
              />
              <button
                className="btn-primary flex items-center gap-1"
                onClick={handleAdd}
                disabled={saving || !newCat.trim()}
              >
                <Plus size={13} /> Add
              </button>
            </div>

            {(error || successMsg) && (
              <div>
                {error && <p className="text-rose-400 text-sm">{error}</p>}
                {successMsg && <p className="text-emerald-400 text-sm">{successMsg}</p>}
              </div>
            )}

            <div className="flex items-center justify-between pt-2 border-t border-slate-700">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                {isDefault ? 'Using defaults' : 'Custom categories'}
              </div>
              <div className="flex gap-2">
                {!isDefault && (
                  <button className="btn-secondary text-xs" onClick={handleReset} disabled={saving}>
                    Reset to Defaults
                  </button>
                )}
                <button className="btn-secondary" onClick={onClose}>Close</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
