import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../services/api'
import { bustUrl } from '../services/cacheBust'
import {
  Plus, Trash2, Edit2, ChevronDown, ChevronRight, PenLine,
  BookOpen, Loader2, CheckCircle2, Sparkles, Search, Eye, EyeOff, Square, CheckSquare, Code, MessageCircle, MessageCircleOff
} from 'lucide-react'
import GlobalSearchModal from '../components/GlobalSearchModal'
import RetroactiveReviewModal from '../components/RetroactiveReviewModal'
import TagChips from '../components/TagChips'
import ProtagonistBadge from '../components/ProtagonistBadge'
import { useUrlModal } from '../hooks/useUrlState'
import BookActionsMenu from '../components/books/BookActionsMenu'
import BookFormModal from '../components/books/BookFormModal'
import PromptEditorModal from '../components/books/PromptEditorModal'
import BookModulesModal from '../components/books/BookModulesModal'
import WordPressPublishModal from '../components/books/WordPressPublishModal'
import CategoryManagerModal from '../components/books/CategoryManagerModal'
import PronounRepairBookModal from '../components/books/PronounRepairBookModal'
import RetranslateModal from '../components/books/RetranslateModal'
import BatchRetranslateModal from '../components/books/BatchRetranslateModal'

export default function Books() {
  const { bookId: bookIdParam } = useParams()
  const expandedBook = bookIdParam ? parseInt(bookIdParam, 10) : null
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // URL-driven modals (push history — back button closes them)
  const searchModal = useUrlModal('search')
  const editBookModal = useUrlModal('editBook', { idKey: 'book' })
  const promptModal = useUrlModal('prompt', { idKey: 'book' })
  const retranslateModal = useUrlModal('retranslate', { paramKeys: ['book', 'ch'] })
  const batchRetranslateModal = useUrlModal('batchRetranslate', { idKey: 'book' })
  const publishModal = useUrlModal('publish', { idKey: 'book' })
  const categoriesModal = useUrlModal('categories', { idKey: 'book' })
  const reviewModal = useUrlModal('review', { idKey: 'book' })
  const pronounRepairModal = useUrlModal('pronounRepair', { idKey: 'book' })
  const modulesModal = useUrlModal('modules', { idKey: 'book' })

  // Chapter selection for batch retranslate — too large to live in the URL,
  // so it's kept local and keyed by book id.
  const [batchChaptersById, setBatchChaptersById] = useState({})

  const [exporting, setExporting] = useState(null) // 'bookId-format' or null
  const [selected, setSelected] = useState({})    // { bookId: Set of chapter numbers }
  const [lastChecked, setLastChecked] = useState({}) // { bookId: chapter number }
  const [batchBusy, setBatchBusy] = useState(false)

  // Book list + expanded book's chapter list are react-query queries.
  // WsQueryBridge invalidates ['books'] / ['chapters'] on translation events,
  // so no manual reload chains are needed here.
  const booksQuery = useQuery({ queryKey: ['books'], queryFn: () => api.listBooks() })
  const books = booksQuery.data?.books || []
  const loading = booksQuery.isPending
  const invalidateBooks = () => queryClient.invalidateQueries({ queryKey: ['books'] })

  const chaptersQuery = useQuery({
    queryKey: ['chapters', expandedBook],
    queryFn: () => api.listChapters(expandedBook),
    enabled: expandedBook != null,
  })
  // Keyed-by-book shape kept so the render code below reads naturally; only
  // the expanded book's chapters are ever displayed.
  const chapters = expandedBook != null && chaptersQuery.data
    ? { [expandedBook]: chaptersQuery.data.chapters || [] }
    : {}
  const invalidateChapters = (bookId) =>
    queryClient.invalidateQueries({ queryKey: ['chapters', bookId] })

  // Ctrl+F opens global search
  useEffect(() => {
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault()
        searchModal.open()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [searchModal])

  const toggleExpand = (bookId) => {
    if (expandedBook === bookId) {
      navigate('/books')
      setSelected(prev => ({ ...prev, [bookId]: new Set() }))
    } else {
      navigate(`/books/${bookId}`)
    }
  }

  const handleDelete = async (id) => {
    if (!confirm('Delete this book and all its chapters?')) return
    await api.deleteBook(id)
    invalidateBooks()
  }

  const handleDeleteChapter = async (bookId, num) => {
    if (!confirm(`Delete chapter ${num}?`)) return
    await api.deleteChapter(bookId, num)
    invalidateChapters(bookId)
  }

  // Original works: create an empty chapter and jump into the write editor.
  const handleNewChapter = async (bookId) => {
    try {
      const res = await api.createChapter(bookId)
      navigate(`/books/${bookId}/chapters/${res.chapter_number}/write`)
    } catch (e) {
      alert(`Failed to create chapter: ${e.message}`)
    }
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

  const handleInvalidateCache = async (bookId) => {
    if (!confirm('Delete the cached EPUB for this book (local disk + Spaces)? The next export will regenerate it.')) return
    try {
      const res = await api.invalidateEpubCache(bookId)
      alert(res?.spaces_purged ? 'EPUB cache cleared (disk + Spaces).' : 'EPUB cache cleared (disk).')
    } catch (e) {
      alert(`Failed to clear EPUB cache: ${e.message}`)
    }
  }

  const togglePublic = async (book) => {
    try {
      await api.updateBook(book.id, { is_public: !book.is_public })
      invalidateBooks()
    } catch (e) {
      alert(`Failed to update visibility: ${e.message}`)
    }
  }

  const toggleComments = async (book) => {
    const next = !(book.comments_enabled !== 0 && book.comments_enabled !== false)
    try {
      await api.setBookCommentsEnabled(book.id, next)
      invalidateBooks()
    } catch (e) {
      alert(`Failed to update comments setting: ${e.message}`)
    }
  }

  const getSelected = (bookId) => selected[bookId] || new Set()
  const selectedCount = (bookId) => getSelected(bookId).size

  const toggleChapter = (bookId, chapterNum, shiftKey) => {
    setSelected(prev => {
      const cur = new Set(prev[bookId] || [])
      const chapterList = (chapters[bookId] || []).map(c => c.chapter)
      if (shiftKey && lastChecked[bookId] != null) {
        const lastIdx = chapterList.indexOf(lastChecked[bookId])
        const curIdx = chapterList.indexOf(chapterNum)
        if (lastIdx !== -1 && curIdx !== -1) {
          const [from, to] = lastIdx < curIdx ? [lastIdx, curIdx] : [curIdx, lastIdx]
          for (let i = from; i <= to; i++) cur.add(chapterList[i])
        }
      } else {
        if (cur.has(chapterNum)) cur.delete(chapterNum)
        else cur.add(chapterNum)
      }
      return { ...prev, [bookId]: cur }
    })
    setLastChecked(prev => ({ ...prev, [bookId]: chapterNum }))
  }

  const selectAll = (bookId) => {
    const all = new Set((chapters[bookId] || []).map(c => c.chapter))
    setSelected(prev => ({ ...prev, [bookId]: all }))
  }

  const unselectAll = (bookId) => {
    setSelected(prev => ({ ...prev, [bookId]: new Set() }))
  }

  const handleBatchDelete = async (bookId) => {
    const nums = [...getSelected(bookId)]
    if (!nums.length) return
    if (!confirm(`Delete ${nums.length} chapter(s)?`)) return
    setBatchBusy(true)
    try {
      await api.batchDeleteChapters(bookId, nums)
      invalidateChapters(bookId)
      unselectAll(bookId)
    } catch (e) { alert(e.message) }
    finally { setBatchBusy(false) }
  }

  const handleBatchProofread = async (bookId) => {
    const nums = [...getSelected(bookId)]
    if (!nums.length) return
    setBatchBusy(true)
    try {
      await api.batchProofread(bookId, nums, true)
      invalidateChapters(bookId)
      unselectAll(bookId)
    } catch (e) { alert(e.message) }
    finally { setBatchBusy(false) }
  }

  const handleBatchRequeue = (bookId) => {
    const nums = [...getSelected(bookId)].sort((a, b) => a - b)
    if (!nums.length) return
    setBatchChaptersById(prev => ({ ...prev, [bookId]: nums }))
    batchRetranslateModal.open(bookId)
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-slate-200">Books</h1>
        <div className="flex items-center gap-2">
          <button
            className="btn-ghost p-2 text-slate-400 hover:text-slate-200"
            onClick={() => searchModal.open()}
            title="Search across books (Ctrl+F)"
          >
            <Search size={16} />
          </button>
          <button className="btn-primary flex items-center gap-1.5" onClick={() => editBookModal.open('new')}>
            <Plus size={14} /> New Book
          </button>
        </div>
      </div>

      {booksQuery.error && (
        <div className="badge-rose mb-4 px-3 py-2 text-sm rounded">{booksQuery.error.message}</div>
      )}

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
                    src={bustUrl(book.cover_thumb_url || `/api/books/${book.id}/cover/thumb`)}
                    alt=""
                    className="w-8 h-11 object-cover rounded border border-slate-700 shrink-0"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-slate-200 truncate">{book.title}</span>
                    <ProtagonistBadge tags={book.tags} size="sm" />
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
                        book.status === 'ongoing-trial' ? 'bg-cyan-500/20 text-cyan-400' :
                        'bg-slate-500/20 text-slate-400'
                      }`}>{book.status}</span>
                    )}
                  </div>
                  {book.tags && book.tags.length > 0 && (
                    <div className="mt-1">
                      <TagChips tags={book.tags} size="sm" />
                    </div>
                  )}
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
                  {/* Comments toggle */}
                  <button
                    className={`btn-ghost p-1.5 ${(book.comments_enabled === 0 || book.comments_enabled === false) ? 'text-rose-400/60' : 'text-indigo-400/70'}`}
                    title={(book.comments_enabled === 0 || book.comments_enabled === false) ? 'Comments disabled (click to enable)' : 'Comments enabled (click to disable)'}
                    onClick={() => toggleComments(book)}
                  >
                    {(book.comments_enabled === 0 || book.comments_enabled === false) ? <MessageCircleOff size={14} /> : <MessageCircle size={14} />}
                  </button>
                  {/* Actions dropdown */}
                  <BookActionsMenu
                    book={book}
                    exporting={exporting}
                    onExport={handleExport}
                    onPublish={() => publishModal.open(book.id)}
                    onCategories={() => categoriesModal.open(book.id)}
                    onReview={() => reviewModal.open(book.id)}
                    onPrompt={() => promptModal.open(book.id)}
                    onApiLogs={() => navigate(`/books/${book.id}/api-calls`)}
                    onEdit={() => editBookModal.open(book.id)}
                    onDelete={() => handleDelete(book.id)}
                    onPronounRepair={() => pronounRepairModal.open(book.id)}
                    onModules={() => modulesModal.open(book.id)}
                    onInvalidateCache={() => handleInvalidateCache(book.id)}
                  />
                </div>
              </div>

              {/* Chapters */}
              {expandedBook === book.id && (
                <div className="border-t border-slate-700 px-4 py-3">
                  {book.is_original && (
                    <div className="mb-2">
                      <button
                        className="btn-primary text-xs px-2.5 py-1 flex items-center gap-1"
                        onClick={() => handleNewChapter(book.id)}
                      >
                        <Plus size={12} /> New Chapter
                      </button>
                    </div>
                  )}
                  {(chapters[book.id] || []).length === 0 ? (
                    <p className="text-xs text-slate-500">No chapters yet.</p>
                  ) : (
                    <>
                      {/* Selection toolbar */}
                      <div className="flex items-center gap-2 mb-2 text-xs">
                        <button className="btn-ghost px-2 py-1 text-xs" onClick={() => selectAll(book.id)}>Select All</button>
                        <button className="btn-ghost px-2 py-1 text-xs" onClick={() => unselectAll(book.id)}>Unselect All</button>
                        <Link to={`/books/${book.id}/api-calls`} className="btn-ghost px-2 py-1 text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1">
                          <Code size={12} /> API Logs
                        </Link>
                        {selectedCount(book.id) > 0 && (
                          <span className="text-slate-400 ml-1">{selectedCount(book.id)} selected</span>
                        )}
                        {selectedCount(book.id) > 0 && (
                          <div className="flex items-center gap-1 ml-auto">
                            <button
                              className="btn-ghost px-2 py-1 text-xs text-emerald-400 hover:text-emerald-300 disabled:opacity-50"
                              disabled={batchBusy}
                              onClick={() => handleBatchProofread(book.id)}
                              title="Mark selected as proofread"
                            >
                              <CheckCircle2 size={12} className="inline mr-1" />Proofread
                            </button>
                            <button
                              className="btn-ghost px-2 py-1 text-xs text-blue-400 hover:text-blue-300 disabled:opacity-50"
                              disabled={batchBusy}
                              onClick={() => handleBatchRequeue(book.id)}
                              title="Requeue selected for retranslation"
                            >
                              <Sparkles size={12} className="inline mr-1" />Requeue
                            </button>
                            <button
                              className="btn-ghost px-2 py-1 text-xs text-rose-400 hover:text-rose-300 disabled:opacity-50"
                              disabled={batchBusy}
                              onClick={() => handleBatchDelete(book.id)}
                              title="Delete selected chapters"
                            >
                              <Trash2 size={12} className="inline mr-1" />Delete
                            </button>
                          </div>
                        )}
                      </div>
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-xs text-slate-500 border-b border-slate-700">
                            <th className="pb-2 w-8" />
                            <th className="text-left pb-2 font-medium">Ch.</th>
                            <th className="text-left pb-2 font-medium">Title</th>
                            <th className="text-left pb-2 font-medium hidden sm:table-cell">Model</th>
                            <th className="text-left pb-2 font-medium hidden sm:table-cell">Date</th>
                            <th className="pb-2" />
                          </tr>
                        </thead>
                        <tbody>
                          {(chapters[book.id] || []).map(ch => {
                            const isChecked = getSelected(book.id).has(ch.chapter)
                            return (
                              <tr key={ch.chapter} className={`border-b border-slate-800 last:border-0 ${isChecked ? 'bg-blue-500/10' : !ch.is_proofread ? 'bg-amber-500/5' : ''}`}>
                                <td className="py-2 w-8 text-center">
                                  <button
                                    className="p-0.5 text-slate-500 hover:text-slate-300"
                                    onClick={(e) => toggleChapter(book.id, ch.chapter, e.shiftKey)}
                                  >
                                    {isChecked
                                      ? <CheckSquare size={14} className="text-blue-400" />
                                      : <Square size={14} />
                                    }
                                  </button>
                                </td>
                                <td className="py-2 text-slate-400 font-mono">
                                  <span className="inline-flex items-center gap-1">
                                    {ch.chapter}
                                    {ch.is_proofread
                                      ? <CheckCircle2 size={11} className="text-emerald-500" title={`Proofread ${new Date(ch.is_proofread).toLocaleDateString()}`} />
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
                                    {!book.is_original && (
                                      <button
                                        className="btn-ghost p-1"
                                        title="Retranslate chapter"
                                        onClick={() => retranslateModal.open({ book: book.id, ch: ch.chapter })}
                                      >
                                        <Sparkles size={12} />
                                      </button>
                                    )}
                                    <Link
                                      to={`/books/${book.id}/chapters/${ch.chapter}/${book.is_original ? 'write' : 'edit'}`}
                                      className="btn-ghost p-1"
                                      title={book.is_original ? 'Write' : 'Edit translation'}
                                    >
                                      <Edit2 size={12} />
                                    </Link>
                                    {!book.is_original && (
                                      <Link
                                        to={`/books/${book.id}/chapters/${ch.chapter}/write`}
                                        className="btn-ghost p-1"
                                        title="Open in write editor (WYSIWYG)"
                                      >
                                        <PenLine size={12} />
                                      </Link>
                                    )}
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
                            )
                          })}
                        </tbody>
                      </table>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Book form modal */}
      {editBookModal.isOpen && (
        <BookFormModal
          book={editBookModal.id === 'new' ? null : books.find(b => b.id === parseInt(editBookModal.id, 10))}
          onClose={editBookModal.close}
          onSaved={() => { editBookModal.close(); invalidateBooks() }}
        />
      )}

      {/* System prompt editor modal */}
      {promptModal.isOpen && (() => {
        const book = books.find(b => b.id === parseInt(promptModal.id, 10))
        if (!book) return null
        return <PromptEditorModal book={book} onClose={promptModal.close} />
      })()}

      {/* Per-book modules modal */}
      {modulesModal.isOpen && (() => {
        const book = books.find(b => b.id === parseInt(modulesModal.id, 10))
        if (!book) return null
        return <BookModulesModal book={book} onClose={modulesModal.close} onSaved={() => { modulesModal.close(); invalidateBooks() }} />
      })()}

      {/* Retranslate modal */}
      {retranslateModal.isOpen && (() => {
        const bId = parseInt(retranslateModal.params.book, 10)
        const chNum = parseInt(retranslateModal.params.ch, 10)
        if (!bId || !chNum) return null
        const chObj = (chapters[bId] || []).find(c => c.chapter === chNum)
        return (
          <RetranslateModal
            bookId={bId}
            chapterNum={chNum}
            chapterTitle={chObj?.title || ''}
            onClose={retranslateModal.close}
          />
        )
      })()}

      {/* Batch retranslate modal */}
      {batchRetranslateModal.isOpen && (() => {
        const bId = parseInt(batchRetranslateModal.id, 10)
        const chs = batchChaptersById[bId] || []
        if (!bId || !chs.length) return null
        return (
          <BatchRetranslateModal
            bookId={bId}
            chapters={chs}
            onClose={batchRetranslateModal.close}
            onDone={() => { unselectAll(bId); batchRetranslateModal.close() }}
          />
        )
      })()}

      {/* WordPress publish modal */}
      {publishModal.isOpen && (() => {
        const book = books.find(b => b.id === parseInt(publishModal.id, 10))
        if (!book) return null
        return <WordPressPublishModal book={book} onClose={publishModal.close} />
      })()}

      {/* Category manager modal */}
      {categoriesModal.isOpen && (() => {
        const book = books.find(b => b.id === parseInt(categoriesModal.id, 10))
        if (!book) return null
        return <CategoryManagerModal book={book} onClose={categoriesModal.close} />
      })()}

      {reviewModal.isOpen && (() => {
        const book = books.find(b => b.id === parseInt(reviewModal.id, 10))
        if (!book) return null
        return <RetroactiveReviewModal book={book} onClose={reviewModal.close} />
      })()}

      {pronounRepairModal.isOpen && (() => {
        const book = books.find(b => b.id === parseInt(pronounRepairModal.id, 10))
        if (!book) return null
        return <PronounRepairBookModal book={book} onClose={pronounRepairModal.close} />
      })()}

      {/* Global search modal */}
      {searchModal.isOpen && (
        <GlobalSearchModal
          books={books}
          onClose={searchModal.close}
        />
      )}
    </div>
  )
}
