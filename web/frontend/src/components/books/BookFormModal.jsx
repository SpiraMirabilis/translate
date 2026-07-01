import { useState, useEffect, useRef } from 'react'
import { api } from '../../services/api'
import { Plus, X, Check, Loader2 } from 'lucide-react'

export default function BookFormModal({ book, onClose, onSaved }) {
  const [form, setForm] = useState({
    title: book?.title || '',
    author: book?.author || '',
    language: book?.language || 'en',
    source_language: book?.source_language || 'zh',
    description: book?.description || '',
    genre: '',
    total_source_chapters: book?.total_source_chapters || '',
    status: book?.status || 'ongoing',
    source_url: book?.source_url || '',
    notes: book?.notes || '',
    tags: Array.isArray(book?.tags) ? book.tags : [],
  })
  const [genres, setGenres] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [coverPreview, setCoverPreview] = useState(book?.cover_image ? `/api/books/${book.id}/cover` : null)
  const [uploadingCover, setUploadingCover] = useState(false)
  const fileInputRef = useRef(null)

  // Tag autocomplete
  const [allTags, setAllTags] = useState([])
  const [tagInput, setTagInput] = useState('')
  const [tagFocused, setTagFocused] = useState(false)

  // Fetch genres on mount (new books only)
  useEffect(() => {
    if (!book) {
      api.listGenres().then(d => setGenres(d.genres || [])).catch(() => {})
    }
  }, [book])

  // Fetch existing tags once for autocomplete
  useEffect(() => {
    api.getAllTags().then(d => setAllTags(d.tags || [])).catch(() => {})
  }, [])

  const addTag = (raw) => {
    const t = (raw || '').trim().toLowerCase()
    if (!t) return
    setForm(f => f.tags.includes(t) ? f : { ...f, tags: [...f.tags, t] })
    setTagInput('')
  }
  const removeTag = (t) => {
    setForm(f => ({ ...f, tags: f.tags.filter(x => x !== t) }))
  }
  const onTagKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag(tagInput)
    } else if (e.key === 'Backspace' && !tagInput && form.tags.length > 0) {
      removeTag(form.tags[form.tags.length - 1])
    }
  }
  const tagSuggestions = tagInput.trim()
    ? allTags
        .filter(t => t.includes(tagInput.trim().toLowerCase()) && !form.tags.includes(t))
        .slice(0, 8)
    : []

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
        const { genre: _genre, source_language: _source_language, ...editForm } = form
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700/60 shrink-0">
          <h2 className="font-semibold text-slate-200">{book ? 'Edit Book' : 'New Book'}</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <div className="px-6 py-4 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-3">
          {/* Genre selector — new books only */}
          {!book && genres.length > 0 && (
            <div className="md:col-span-2">
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
          <div><label className="label">Source Language</label><input className="input" value={form.source_language} onChange={e => setForm(f => ({...f, source_language: e.target.value}))} placeholder="zh" /></div>
          <div><label className="label">Target Language</label><input className="input" value={form.language} onChange={e => setForm(f => ({...f, language: e.target.value}))} placeholder="en" /></div>
          <div><label className="label">Source URL</label><input className="input" value={form.source_url} onChange={e => setForm(f => ({...f, source_url: e.target.value}))} placeholder="https://..." /></div>
          <div>
            <label className="label">Status</label>
            <select className="input" value={form.status} onChange={e => setForm(f => ({...f, status: e.target.value}))}>
              <option value="ongoing">Ongoing</option>
              <option value="ongoing-trial">Ongoing (Trial)</option>
              <option value="hiatus">Hiatus</option>
              <option value="completed">Completed</option>
              <option value="dropped">Dropped</option>
            </select>
          </div>
          <div>
            <label className="label">Total Source Chapters</label>
            <input className="input" type="number" min="0" placeholder="Optional" value={form.total_source_chapters} onChange={e => setForm(f => ({...f, total_source_chapters: e.target.value}))} />
          </div>
          <div><label className="label">Description</label><textarea className="input h-24 resize-none" value={form.description} onChange={e => setForm(f => ({...f, description: e.target.value}))} /></div>
          <div><label className="label">Notes</label><textarea className="input h-24 resize-none" value={form.notes} onChange={e => setForm(f => ({...f, notes: e.target.value}))} /></div>
          {book && (
            <div className="relative md:col-span-2">
              <label className="label">Tags</label>
              <div className="input flex flex-wrap items-center gap-1 min-h-[2.25rem] py-1.5">
                {form.tags.map(t => (
                  <span key={t} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-slate-700 text-slate-200">
                    {t}
                    <button type="button" className="text-slate-400 hover:text-rose-400" onClick={() => removeTag(t)} title="Remove tag">
                      <X size={11} />
                    </button>
                  </span>
                ))}
                <input
                  className="flex-1 min-w-[8rem] bg-transparent outline-none text-sm text-slate-200 placeholder:text-slate-500"
                  value={tagInput}
                  onChange={e => setTagInput(e.target.value)}
                  onKeyDown={onTagKeyDown}
                  onFocus={() => setTagFocused(true)}
                  onBlur={() => setTimeout(() => setTagFocused(false), 150)}
                  placeholder={form.tags.length === 0 ? 'e.g. xianxia, female protagonist' : ''}
                />
              </div>
              {tagFocused && tagSuggestions.length > 0 && (
                <div className="absolute z-10 left-0 right-0 mt-1 max-h-48 overflow-auto rounded border border-slate-700 bg-slate-800 shadow-lg">
                  {tagSuggestions.map(s => (
                    <button
                      key={s}
                      type="button"
                      className="block w-full text-left px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700"
                      onMouseDown={(e) => { e.preventDefault(); addTag(s) }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
              <p className="text-xs text-slate-500 mt-1">Press <kbd className="px-1 bg-slate-800 rounded">Enter</kbd> or <kbd className="px-1 bg-slate-800 rounded">,</kbd> to add. Lowercased on save. <code className="text-slate-400">female protagonist</code> / <code className="text-slate-400">male protagonist</code> render a prominent badge.</p>
            </div>
          )}

          {/* Cover image */}
          {book && (
            <div className="md:col-span-2">
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

        <div className="px-6 py-3 border-t border-slate-700/60 flex items-center justify-between gap-2 shrink-0">
          {error ? <p className="text-rose-400 text-sm">{error}</p> : <span />}
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={onClose}>Cancel</button>
            <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
