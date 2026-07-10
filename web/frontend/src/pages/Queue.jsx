import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useWsEvent } from '../hooks/useWsEvent'
import {
  Play, Trash2, Upload, FileText, Loader2, ListChecks, X, StopCircle, RefreshCw, Info
} from 'lucide-react'
import TranslationProgress from '../components/TranslationProgress'
import ComboBox from '../components/ComboBox'

export default function Queue() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [filterBook, setFilterBook] = useLocalStorage('queue.filterBook', '')
  const [processing, setProcessing] = useState(false)
  const [jobStatus, setJobStatus] = useState('idle')
  const [showUpload, setShowUpload] = useState(false)
  const [error, setError] = useState(null)
  const [chunkProgress, setChunkProgress] = useState(null)
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

  const booksQuery = useQuery({ queryKey: ['books'], queryFn: () => api.listBooks() })
  const books = booksQuery.data?.books || []

  const providersQuery = useQuery({ queryKey: ['providers'], queryFn: () => api.listProviders() })
  const providers = providersQuery.data?.providers || []

  useEffect(() => {
    api.getJobStatus().then(d => {
      setJobStatus(d.status)
      if (d.is_running) setProcessing(true)
      if (d.auto_process) setAutoProcess(true)
    }).catch(() => {})
  }, [])

  // Watch for job events to update UI. useWsEvent delivers every message via
  // the WS fan-out (no lastMessage state, no missed/replayed-stale messages);
  // the handler closure is always the latest render's, so autoProcess/load are
  // read fresh without ref plumbing or dedup.
  useWsEvent((msg) => {
    // Replayed terminal events (re-sent on every socket accept) must not
    // repaint status: on first load they can race the mount-time
    // getJobStatus fetch and a days-old complete/error would win. Live
    // state comes from that fetch and the ws_reconnected catch-up.
    if (msg.replayed && (msg.type === 'translation_complete' || msg.type === 'error'
                         || msg.type === 'auto_process_done'
                         || msg.type === 'translation_cancelled')) {
      return
    }
    if (msg.type === 'ws_reconnected') {
      // One-shot catch-up after a reconnect: WsQueryBridge refetches the queue
      // list (blanket invalidation); refresh local job status here.
      api.getJobStatus().then(d => {
        setJobStatus(d.status)
        setProcessing(!!d.is_running)
        if (d.auto_process) setAutoProcess(true)
      }).catch(() => {})
    }
    if (msg.type === 'progress') {
      setChunkProgress(msg)
      setJobStatus(msg.phase === 'session_limit' ? 'waiting' : 'running')
    }
    if (msg.type === 'translation_complete') {
      setChunkProgress(null)
      // Queue list refetch is handled by WsQueryBridge's invalidation.
      // During auto-process the backend drives the loop, so stay in "running".
      // For single-shot, mark complete.
      if (!autoProcess) {
        setJobStatus('complete')
        setProcessing(false)
      }
    }
    if (msg.type === 'auto_process_done') {
      setChunkProgress(null)
      setProcessing(false)
      setJobStatus('complete')
      setAutoProcess(false)
      // Queue list refetch is handled by WsQueryBridge's invalidation.
    }
    if (msg.type === 'auto_process_stopping') {
      // Visual feedback — backend acknowledged, will stop after current chapter
    }
    if (msg.type === 'error') {
      setProcessing(false)
      setJobStatus('error')
      setChunkProgress(null)
      setAutoProcess(false)
    }
    if (msg.type === 'entity_review_needed') {
      setJobStatus('awaiting_review')
      navigate('/')
    }
    if (msg.type === 'json_fix_needed') {
      setJobStatus('awaiting_json_fix')
      navigate('/')
    }
  })

  const handleProcessNext = async () => {
    setProcessing(true)
    setError(null)
    try {
      await api.processNext({
        book_id: filterBook ? parseInt(filterBook) : null,
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
      setJobStatus('running')
    } catch (e) {
      setError(e.message)
      setProcessing(false)
    }
  }

  const handleRemove = async (id) => {
    await api.removeQueueItem(id)
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

  const isJobRunning = processing || jobStatus === 'running' || jobStatus === 'waiting' || jobStatus === 'awaiting_review'

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
            <button className="btn-danger flex items-center gap-1.5 text-xs" onClick={handleClear}>
              <X size={13} /> Clear Queue
            </button>
          )}
          {isJobRunning && autoProcess ? (
            <button
              className="btn-danger flex items-center gap-1.5"
              onClick={async () => {
                try { await api.stopAutoProcess() } catch { /* ignore */ }
                setAutoProcess(false)
              }}
              title="Finish the current chapter then stop"
            >
              <StopCircle size={13} /> Stop after current
            </button>
          ) : (
            <button
              className="btn-primary flex items-center gap-1.5"
              onClick={handleProcessNext}
              disabled={isJobRunning || queue.length === 0}
            >
              {isJobRunning
                ? <><Loader2 size={13} className="animate-spin" /> Processing…</>
                : autoProcess
                  ? <><RefreshCw size={13} /> Start Auto-process</>
                  : <><Play size={13} /> Process Next</>}
            </button>
          )}
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
                if (!val && processing) {
                  api.stopAutoProcess().catch(() => {})
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

      {/* Job status banner */}
      {isJobRunning && (
        <div className="card p-4 mb-4 border-indigo-700 bg-indigo-950/40 space-y-3">
          {jobStatus === 'awaiting_review' ? (
            <div className="flex items-center gap-2">
              <Loader2 size={14} className="text-amber-400" />
              <span className="text-sm text-amber-300">
                Waiting for entity review — go to the Translate tab
              </span>
            </div>
          ) : (
            <div className="space-y-2">
              <TranslationProgress progress={chunkProgress} status={jobStatus} />
              {autoProcess && (
                <p className="text-xs text-slate-500">
                  Auto-processing{maxChapters ? ` (limit: ${maxChapters})` : ''} — {queue.length} chapter{queue.length !== 1 ? 's' : ''} remaining
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Filter */}
      <div className="mb-4">
        <select className="input w-48" value={filterBook} onChange={e => setFilterBook(e.target.value)}>
          <option value="">All books</option>
          {books.filter(b => queuedBookIds.includes(b.id)).map(b => <option key={b.id} value={b.id}>{b.id}: {b.title}</option>)}
        </select>
      </div>

      {(error || queueQuery.error) && (
        <p className="text-rose-400 text-sm mb-4">{error || queueQuery.error.message}</p>
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
          {queue.map((item, i) => (
            <div key={item.id} className="flex items-center gap-3 px-4 py-3">
              <span className="text-xs text-slate-600 w-5 text-right">{i + 1}</span>
              <FileText size={14} className="text-slate-500 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-200 truncate">{item.title || `Item ${item.id}`}</p>
                <p className="text-xs text-slate-500">
                  {item.book_title || `Book ${item.book_id}`}
                  {item.chapter_number ? ` · Ch. ${item.chapter_number}` : ''}
                </p>
              </div>
              <button
                className="btn-ghost p-1.5 hover:text-rose-400 shrink-0"
                onClick={() => handleRemove(item.id)}
                disabled={isJobRunning}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
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

  const isEpub = files.length === 1 && files[0].name.toLowerCase().endsWith('.epub')
  const isFb2 = files.length === 1 && /\.fb2(\.zip)?$/.test(files[0].name.toLowerCase())
  const isJson = files.length === 1 && files[0].name.toLowerCase().endsWith('.json')
  const isBook = isEpub || isFb2 || isJson  // single-file book formats: create-book support
  const isBatch = files.length > 1

  const handleFileChange = (e) => {
    const selected = Array.from(e.target.files || [])
    setFiles(selected)
    setCreateBook(false)
  }

  const handleUpload = async () => {
    if (files.length === 0) { setError('No file selected'); return }
    if (!isBook && !bookId) { setError('Select a book'); return }
    if (isBook && !bookId && !createBook) { setError('Select a book'); return }

    setUploading(true); setError(null)
    try {
      if (isBook) {
        const fd = new FormData()
        fd.append('file', files[0])
        if (bookId) fd.append('book_id', bookId)
        fd.append('create_book', createBook ? 'true' : 'false')
        await (isJson ? api.uploadJson(fd) : isFb2 ? api.uploadFb2(fd) : api.uploadEpub(fd))
      } else if (isBatch) {
        const fd = new FormData()
        for (const f of files) fd.append('files', f)
        fd.append('book_id', bookId)
        if (chapterNum) fd.append('start_chapter', chapterNum)
        await api.uploadBatch(fd)
      } else {
        const fd = new FormData()
        fd.append('file', files[0])
        fd.append('book_id', bookId)
        if (chapterNum) fd.append('chapter_number', chapterNum)
        await api.uploadToQueue(fd)
      }
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

        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex items-center gap-1.5" onClick={handleUpload} disabled={uploading}>
            {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
            Upload{isBatch ? ` ${files.length} files` : ''}
          </button>
        </div>
      </div>
    </div>
  )
}
