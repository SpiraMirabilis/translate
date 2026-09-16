import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useJobs } from '../hooks/useJobs'
import {
  Play, Trash2, Upload, FileText, Loader2, ListChecks, X, StopCircle, RefreshCw, Info
} from 'lucide-react'
import JobList from '../components/jobs/JobList'
import ComboBox from '../components/ComboBox'

export default function Queue() {
  const queryClient = useQueryClient()
  const [filterBook, setFilterBook] = useLocalStorage('queue.filterBook', '')
  const [showUpload, setShowUpload] = useState(false)
  const [error, setError] = useState(null)

  // Job state is shared and keyed by book — several can run at once.
  const {
    isBookRunning, atCapacity, maxConcurrent, markStarted,
    active: activeJobsList, jobFor, refresh: refreshJobs,
  } = useJobs()
  const [translationModel, setTranslationModel] = useLocalStorage('queue.translationModel', '')
  const [adviceModel, setAdviceModel]             = useLocalStorage('shared.adviceModel', '')
  const [cleaningModel, setCleaningModel]         = useLocalStorage('shared.cleaningModel', '')
  const [noReview, setNoReviewRaw]                = useLocalStorage('queue.noReview', false)
  const [twoPass, setTwoPassRaw]                  = useLocalStorage('queue.twoPass', false)
  // Mutually exclusive: two-pass review is meaningless when entity review is skipped
  const setNoReview = (v) => { setNoReviewRaw(v); if (v) setTwoPassRaw(false) }
  const setTwoPass = (v) => { setTwoPassRaw(v); if (v) setNoReviewRaw(false) }
  // Stale localStorage can have both set, which disables both checkboxes. Reset to neither.
  useEffect(() => {
    if (noReview && twoPass) { setNoReviewRaw(false); setTwoPassRaw(false) }
  }, [])
  const [noClean, setNoClean]                     = useLocalStorage('queue.noClean', false)
  const [noStream, setNoStream]                   = useLocalStorage('queue.noStream', false)
  const [saveAsDraft, setSaveAsDraft]             = useLocalStorage('queue.saveAsDraft', false)
  const [autoProcess, setAutoProcess]             = useLocalStorage('queue.autoProcess', false)
  const [maxChapters, setMaxChapters]             = useLocalStorage('queue.maxChapters', '')

  const queueQuery = useQuery({
    queryKey: ['queue', filterBook || null],
    queryFn: () => api.listQueue(filterBook ? parseInt(filterBook) : undefined),
  })
  const queue = queueQuery.data?.items || []
  // Which books currently have queued chapters — the response includes the
  // full distinct set (independent of the active filter), so the drop-down
  // stays correct without a second unfiltered fetch. Fall back to deriving
  // it from items in case an older backend doesn't send book_ids.
  const queuedBookIds = queueQuery.data?.book_ids || [...new Set(queue.map(it => it.book_id))]
  const loading = queueQuery.isPending
  const invalidateQueue = () => queryClient.invalidateQueries({ queryKey: ['queue'] })

  // Pickers only need id + title; ['books', …] keeps WsQueryBridge's
  // prefix invalidation working.
  const booksQuery = useQuery({ queryKey: ['books', 'minimal'], queryFn: () => api.listBooksMinimal() })
  const books = booksQuery.data?.books || []
  // The filter drop-down is built from `books`; when that fetch is slow, fails
  // or is starved (it's the heaviest admin call), the list silently collapses
  // to "All books" and looks like a queue bug. Surface the real state instead.
  const booksBroken = !booksQuery.isPending && books.length === 0

  const providersQuery = useQuery({ queryKey: ['providers'], queryFn: () => api.listProviders() })
  const providers = providersQuery.data?.providers || []

  // No local job state and no WebSocket handler here: both live in useJobs, so
  // this page and Dashboard can no longer disagree about what is running.
  // Prompts are shown by the global PromptHost, so there is nothing to
  // navigate to either.

  const runOptions = () => ({
    translation_model: translationModel || null,
    advice_model: adviceModel || null,
    cleaning_model: cleaningModel || null,
    no_review: noReview,
    two_pass: twoPass,
    no_clean: noClean,
    no_stream: noStream,
    save_as_draft: saveAsDraft,
    auto_process: autoProcess,
    max_chapters: autoProcess && maxChapters ? parseInt(maxChapters) : null,
  })

  const handleProcessAll = async () => {
    setError(null)
    try {
      const res = await api.processAllBooks(runOptions())
      const blocked = (res.skipped || []).filter(s => s.reason === 'at_capacity')
      if (blocked.length) {
        setError(`Started ${res.started.length}; ${blocked.length} book(s) waiting for a free slot.`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      refreshJobs()
    }
  }

  const handleProcessNext = async () => {
    setError(null)
    const bookId = filterBook ? parseInt(filterBook) : null
    markStarted(bookId)
    try {
      await api.processNext({
        book_id: bookId,
        translation_model: translationModel || null,
        advice_model: adviceModel || null,
        cleaning_model: cleaningModel || null,
        no_review: noReview,
        two_pass: twoPass,
        no_clean: noClean,
        no_stream: noStream,
        save_as_draft: saveAsDraft,
        auto_process: autoProcess,
        max_chapters: autoProcess && maxChapters ? parseInt(maxChapters) : null,
      })
    } catch (e) {
      setError(e.message)
    } finally {
      refreshJobs()
    }
  }

  const handleCancelJob = async (bookId) => {
    try { await api.cancelJob(bookId) } catch { /* ignore */ }
    refreshJobs()
  }

  const handleStopAuto = async (bookId) => {
    try { await api.stopAutoProcess(bookId) } catch { /* ignore */ }
    refreshJobs()
  }

  const handleRemove = async (id) => {
    try {
      await api.removeQueueItem(id)
    } catch (e) {
      setError(e.message)
      return
    }
    invalidateQueue()
  }

  const handleRelease = async (id) => {
    try {
      await api.releaseQueueItem(id)
    } catch (e) {
      setError(e.message)
      return
    }
    invalidateQueue()
  }

  const handleClear = async () => {
    const bookName = books.find(b => String(b.id) === String(filterBook))?.title || `Book ${filterBook}`
    if (!confirm(`Clear all queued chapters for "${bookName}"?`)) return
    try {
      await api.clearQueue(parseInt(filterBook))
    } catch (e) {
      setError(e.message)
      return
    }
    invalidateQueue()
  }

  // Per book, not global: a job on another book must not disable this book's
  // controls. Only the capacity check stays global.
  const filterBookId = filterBook ? parseInt(filterBook) : null
  const filterBookRunning = isBookRunning(filterBookId)
  const filterBookJob = jobFor(filterBookId)

  const modelOptions = providers.flatMap(p =>
    (p.models || []).map(m => `${p.name}:${m}`)
  )

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-5 flex-wrap gap-2">
        <h1 className="text-lg font-semibold text-slate-200">Queue</h1>
        <div className="flex gap-2 flex-wrap">
          <button className="btn-secondary flex items-center gap-1.5 text-xs" onClick={() => setShowUpload(true)}>
            <Upload size={13} /> Upload File
          </button>
          {queue.length > 0 && filterBook && (
            <button
              className="btn-danger flex items-center gap-1.5 text-xs"
              onClick={handleClear}
              disabled={filterBookRunning}
              title={filterBookRunning ? 'This book is translating' : undefined}
            >
              <X size={13} /> Clear Queue
            </button>
          )}
          {filterBookRunning && autoProcess && filterBookJob?.auto_process ? (
            <button
              className="btn-danger flex items-center gap-1.5"
              onClick={() => handleStopAuto(filterBookId)}
              title="Finish the current chapter then stop"
            >
              <StopCircle size={13} /> Stop after current
            </button>
          ) : (
            <button
              className="btn-primary flex items-center gap-1.5"
              onClick={handleProcessNext}
              disabled={filterBookRunning || atCapacity || queue.length === 0}
              title={
                filterBookRunning ? 'This book is already translating'
                  : atCapacity ? `All ${maxConcurrent} translation slots are in use`
                  : undefined
              }
            >
              {filterBookRunning
                ? <><Loader2 size={13} className="animate-spin" /> Processing…</>
                : autoProcess
                  ? <><RefreshCw size={13} /> Start Auto-process</>
                  : <><Play size={13} /> Process Next</>}
            </button>
          )}
          {/* One worker per queued book, up to the concurrency limit. */}
          <button
            className="btn-primary flex items-center gap-1.5"
            onClick={handleProcessAll}
            disabled={atCapacity || queuedBookIds.length === 0}
            title={atCapacity
              ? `All ${maxConcurrent} translation slots are in use`
              : 'Start one worker per book with queued chapters'}
          >
            <RefreshCw size={13} /> Process All Books
          </button>
        </div>
      </div>

      {/* Model settings */}
      <div className="card p-4 mb-5">
        <p className="text-xs font-medium text-slate-400 mb-3">Translation settings for next job</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="label">Translation model</label>
            <ComboBox
              value={translationModel}
              onChange={setTranslationModel}
              options={modelOptions}
              placeholder="Default"
            />
          </div>
          <div>
            <label className="label flex items-center gap-1">
              Advice model
              <span className="relative group">
                <Info size={11} className="text-slate-500 hover:text-slate-300 cursor-help" />
                <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-56 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                  Suggests translations for new entity names. A small, cheap model works well here — e.g. oai:gpt-5-mini or claude:claude-haiku-4-5.
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
                  Filters out common words misidentified as entities. A small, cheap model works well — e.g. oai:gpt-5-mini or claude:claude-haiku-4-5.
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
        <div className="flex items-center gap-x-6 gap-y-2 mt-3 flex-wrap">
          <label
            className={`flex items-center gap-2 text-sm select-none ${twoPass ? 'text-slate-500 cursor-not-allowed' : 'text-slate-300 cursor-pointer'}`}
            title={twoPass ? 'Disabled because Two-pass is on' : ''}
          >
            <input
              type="checkbox"
              checked={noReview}
              disabled={twoPass}
              onChange={e => setNoReview(e.target.checked)}
            />
            Skip entity review
          </label>
          <label
            className={`flex items-center gap-2 text-sm select-none ${noReview ? 'text-slate-500 cursor-not-allowed' : 'text-slate-300 cursor-pointer'}`}
            title={noReview ? 'Disabled because Skip Review is on' : ''}
          >
            <input
              type="checkbox"
              checked={twoPass}
              disabled={noReview}
              onChange={e => setTwoPass(e.target.checked)}
            />
            Two-pass review
            <span className="relative group">
              <Info size={13} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-64 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                Identifies and translates entities in a first pass, then waits for your review before translating the chapter prose. Your edited entity names are used directly by the model in the second pass — no after-the-fact substitution. Doubles input tokens; output tokens unchanged.
              </span>
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={noClean}
              onChange={e => setNoClean(e.target.checked)}
            />
            Skip entity cleaning
            <span className="relative group">
              <Info size={13} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-64 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                A second pass using the cleaning model to ensure new entities are only proper nouns. Recommended when using DeepSeek or smaller parameter models, which tend to classify generic terms as entities. Uses very few output tokens, and cleaning model is recommended to be a mini-model like Claude Haiku or gpt-5-mini, or similar.
              </span>
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={noStream}
              onChange={e => setNoStream(e.target.checked)}
            />
            Disable streaming
            <span className="relative group">
              <Info size={13} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-64 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                Wait for each chunk to complete before processing instead of streaming tokens. The progress bar won&apos;t update during generation, but useful for diagnosing provider issues or when streaming is unreliable.
              </span>
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={saveAsDraft}
              onChange={e => setSaveAsDraft(e.target.checked)}
            />
            Save as drafts
            <span className="relative group">
              <Info size={13} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-64 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                Translated chapters are saved unpublished — invisible on the public site until you publish them (per chapter, or batch-publish with a schedule from the Books page). Default is to publish immediately.
              </span>
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoProcess}
              onChange={e => {
                const val = e.target.checked
                setAutoProcess(val)
                if (!val && filterBookRunning) {
                  api.stopAutoProcess(filterBookId).catch(() => {})
                }
              }}
            />
            Auto-process queue
          </label>
          {autoProcess && (
            <input
              type="number"
              min="1"
              placeholder="All"
              value={maxChapters}
              onChange={e => setMaxChapters(e.target.value)}
              className="input w-20 text-xs"
              title="Max chapters to process (blank = all)"
            />
          )}
        </div>
      </div>

      {/* One card per running book. The old single banner also told the user to
          "go to the Translate tab" for a prompt; PromptHost now shows prompts
          wherever they are. */}
      {activeJobsList.length > 0 && (
        <div className="card p-4 mb-4 border-indigo-700 bg-indigo-950/40 space-y-3">
          <JobList onCancel={handleCancelJob} onStopAuto={handleStopAuto} />
          {autoProcess && (
            <p className="text-xs text-slate-500">
              Auto-processing{maxChapters ? ` (limit: ${maxChapters})` : ''} — {queue.length} chapter{queue.length !== 1 ? 's' : ''} remaining
            </p>
          )}
        </div>
      )}

      {/* Filter */}
      <div className="mb-4 flex items-center gap-3">
        <select className="input w-48" value={filterBook} onChange={e => setFilterBook(e.target.value)}>
          <option value="">All books</option>
          {books.filter(b => queuedBookIds.includes(b.id)).map(b => <option key={b.id} value={b.id}>{b.id}: {b.title}</option>)}
          {/* A saved filter whose book has since drained out of the queue is no
              longer in the options above — without this the <select> renders as
              "All books" while still filtering to an empty book. */}
          {filterBook && !books.some(b => queuedBookIds.includes(b.id) && String(b.id) === String(filterBook)) && (
            <option value={filterBook}>
              {books.find(b => String(b.id) === String(filterBook))?.title
                ? `${filterBook}: ${books.find(b => String(b.id) === String(filterBook)).title} (no queued chapters)`
                : `Book ${filterBook} (no queued chapters)`}
            </option>
          )}
        </select>
        {booksQuery.isPending && (
          <span className="text-xs text-slate-500 flex items-center gap-1">
            <Loader2 size={12} className="animate-spin" /> loading books…
          </span>
        )}
        {booksBroken && (
          <button className="btn-ghost text-xs text-amber-400 flex items-center gap-1"
                  onClick={() => booksQuery.refetch()}
                  title={booksQuery.error?.message || 'The book list came back empty'}>
            <RefreshCw size={12} /> book list unavailable — retry
          </button>
        )}
      </div>

      {(error || queueQuery.error || booksQuery.error) && (
        <p className="text-rose-400 text-sm mb-4">
          {error || queueQuery.error?.message || `Book list: ${booksQuery.error.message}`}
        </p>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-slate-400 text-sm"><Loader2 size={14} className="animate-spin" /> Loading…</div>
      ) : queue.length === 0 ? (
        <div className="card p-8 text-center text-slate-500">
          <ListChecks size={32} className="mx-auto mb-3 opacity-40" />
          <p>Queue is empty. Upload files to add chapters.</p>
        </div>
      ) : (
        <div className="card divide-y divide-slate-700">
          {queue.map((item, i) => {
            const processing = item.status === 'processing'
            return (
              <div key={item.id} className="flex items-center gap-3 px-4 py-3">
                <span className="text-xs text-slate-600 w-5 text-right">{i + 1}</span>
                {processing
                  ? <Loader2 size={14} className="text-amber-400 shrink-0 animate-spin" />
                  : <FileText size={14} className="text-slate-500 shrink-0" />}
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-slate-200 truncate">
                    {item.title || `Item ${item.id}`}
                    {processing && (
                      <span className="ml-2 align-middle text-[10px] uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-1 py-0.5">
                        In progress
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-slate-500">
                    {item.book_title || `Book ${item.book_id}`}
                    {item.chapter_number ? ` · Ch. ${item.chapter_number}` : ''}
                  </p>
                </div>
                {/* A claimed row is strandable only when ITS OWN book has no live
                    worker — a job on another book has no claim on it, and gating
                    on "anything running" made the escape hatch useless as soon as
                    a second book started. */}
                {processing && !isBookRunning(item.book_id) && (
                  <button
                    className="btn-ghost p-1.5 text-xs hover:text-amber-300 shrink-0"
                    onClick={() => handleRelease(item.id)}
                    title="Worker is gone — return this chapter to the queue"
                  >
                    <RefreshCw size={13} />
                  </button>
                )}
                <button
                  className="btn-ghost p-1.5 hover:text-rose-400 shrink-0"
                  onClick={() => handleRemove(item.id)}
                  disabled={processing || isBookRunning(item.book_id)}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            )
          })}
        </div>
      )}

      {showUpload && (
        <UploadModal books={books} onClose={() => setShowUpload(false)} onDone={() => { setShowUpload(false); invalidateQueue() }} />
      )}
    </div>
  )
}


function UploadModal({ books, onClose, onDone }) {
  const [files, setFiles] = useState([])
  const [bookId, setBookId] = useState('')
  const [chapterNum, setChapterNum] = useState('')
  const [createBook, setCreateBook] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)
  // Set when a bulk upload reports chapters that already exist; drives the
  // Keep / Discard prompt. { label, numbers }
  const [conflict, setConflict] = useState(null)

  const isEpub = files.length === 1 && files[0].name.toLowerCase().endsWith('.epub')
  const isFb2 = files.length === 1 && /\.fb2(\.zip)?$/.test(files[0].name.toLowerCase())
  const isJson = files.length === 1 && files[0].name.toLowerCase().endsWith('.json')
  const isBook = isEpub || isFb2 || isJson  // single-file book formats: create-book support
  const isBatch = files.length > 1

  const handleFileChange = (e) => {
    const selected = Array.from(e.target.files || [])
    setFiles(selected)
    setCreateBook(false)
    setConflict(null)
  }

  // One upload attempt. `onConflict` is 'ask' (default — backend reports
  // collisions), 'keep' (queue duplicates anyway), or 'discard' (skip them).
  const doUpload = async (onConflict) => {
    if (isBook) {
      const fd = new FormData()
      fd.append('file', files[0])
      if (bookId) fd.append('book_id', bookId)
      fd.append('create_book', createBook ? 'true' : 'false')
      fd.append('on_conflict', onConflict)
      return isJson ? api.uploadJson(fd) : isFb2 ? api.uploadFb2(fd) : api.uploadEpub(fd)
    }
    if (isBatch) {
      const fd = new FormData()
      for (const f of files) fd.append('files', f)
      fd.append('book_id', bookId)
      if (chapterNum) fd.append('start_chapter', chapterNum)
      fd.append('on_conflict', onConflict)
      return api.uploadBatch(fd)
    }
    // Single text file: no duplicate prompt.
    const fd = new FormData()
    fd.append('file', files[0])
    fd.append('book_id', bookId)
    if (chapterNum) fd.append('chapter_number', chapterNum)
    return api.uploadToQueue(fd)
  }

  const handleUpload = async () => {
    if (files.length === 0) { setError('No file selected'); return }
    if (!isBook && !bookId) { setError('Select a book'); return }
    if (isBook && !bookId && !createBook) { setError('Select a book'); return }

    setUploading(true); setError(null)
    try {
      const res = await doUpload('ask')
      if (res && res.status === 'conflict') {
        setConflict({ label: res.existing_label, numbers: res.existing_numbers })
        setUploading(false)
        return
      }
      onDone()
    } catch (e) {
      setError(e.message); setUploading(false)
    }
  }

  const resolveConflict = async (mode) => {  // 'keep' | 'discard'
    setConflict(null); setUploading(true); setError(null)
    try {
      await doUpload(mode)
      onDone()
    } catch (e) {
      setError(e.message); setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">Upload to Queue</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="label">Files (.txt, .epub, .fb2 or .json)</label>
            <input type="file" accept=".txt,.epub,.fb2,.zip,.json" multiple className="input py-1 text-sm" onChange={handleFileChange} />
            {isBatch && (
              <p className="text-xs text-slate-500 mt-1">
                {files.length} text files selected — will be sorted by chapter number or filename
              </p>
            )}
          </div>

          <div>
            <label className="label">Book {isBook ? '' : '*'}</label>
            <select
              className="input"
              value={createBook ? '__create__' : bookId}
              onChange={e => {
                if (e.target.value === '__create__') {
                  setCreateBook(true)
                  setBookId('')
                } else {
                  setCreateBook(false)
                  setBookId(e.target.value)
                }
              }}
            >
              <option value="">Select…</option>
              {isBook && <option value="__create__">Create book from this {isJson ? 'JSON' : isFb2 ? 'FB2' : 'EPUB'}</option>}
              {books.map(b => <option key={b.id} value={b.id}>{b.id}: {b.title}</option>)}
            </select>
          </div>
          {!isBook && (
            <div>
              <label className="label">{isBatch ? 'Starting chapter # (optional)' : 'Chapter # (optional)'}</label>
              <input className="input" type="number" min="1" value={chapterNum} onChange={e => setChapterNum(e.target.value)}
                placeholder={isBatch ? 'Auto-detect from filenames' : ''}
              />
              {isBatch && (
                <p className="text-xs text-slate-500 mt-1">
                  Chapter numbers are extracted from filenames when possible (e.g. chapter_001.txt, 003.txt)
                </p>
              )}
            </div>
          )}
        </div>

        {error && <p className="text-rose-400 text-sm">{error}</p>}

        {conflict ? (
          <div className="space-y-3 rounded border border-amber-700/50 bg-amber-950/30 p-3">
            <p className="text-sm text-amber-200">
              Chapter{conflict.numbers?.length === 1 ? '' : 's'} {conflict.label} already exist{conflict.numbers?.length === 1 ? 's' : ''} in this book.
            </p>
            <p className="text-xs text-slate-400">
              <strong className="text-slate-300">Keep</strong> queues them anyway (re-translates/overwrites).{' '}
              <strong className="text-slate-300">Discard</strong> skips the duplicates and queues only the new chapters.
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-secondary" onClick={() => setConflict(null)} disabled={uploading}>Cancel</button>
              <button className="btn-secondary" onClick={() => resolveConflict('discard')} disabled={uploading}>Discard</button>
              <button className="btn-primary" onClick={() => resolveConflict('keep')} disabled={uploading}>Keep</button>
            </div>
          </div>
        ) : (
          <div className="flex justify-end gap-2">
            <button className="btn-secondary" onClick={onClose}>Cancel</button>
            <button className="btn-primary flex items-center gap-1.5" onClick={handleUpload} disabled={uploading}>
              {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
              Upload{isBatch ? ` ${files.length} files` : ''}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
