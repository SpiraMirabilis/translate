/**
 * Dashboard — main translation workspace.
 *
 * Left panel:  input, book/chapter selector, model override, translate button
 * Right panel: persistent activity log + one progress card per running book
 *
 * Several books can translate at once, so this page owns no job state: it
 * reads useJobs() and the interactive prompts are hosted globally by
 * PromptHost (they can arrive while you are on any page).
 */
import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { useWsEvent } from '../hooks/useWsEvent'
import ErrorState from '../components/ErrorState'
import JobList from '../components/jobs/JobList'
import ComboBox from '../components/ComboBox'
import { useJobs } from '../hooks/useJobs'
import { useLocalStorage } from '../hooks/useLocalStorage'
import {
  Play, Info, Trash2, ListPlus
} from 'lucide-react'

export default function Dashboard() {
  const queryClient = useQueryClient()
  const [inputText, setInputText] = useState('')
  // Book + chapter selection persist in localStorage so the paste→translate
  // workflow survives reloads. The chapter number auto-increments after each
  // successful translation (see the translation_complete handler) so the user
  // can just paste the next chapter and hit Translate without re-typing it.
  const [selectedBook, setSelectedBook] = useLocalStorage('dashboard.book', '')
  const [chapterNum, setChapterNum] = useLocalStorage('dashboard.chapter', '')
  const [modelOverride, setModelOverride] = useLocalStorage('dashboard.modelOverride', '')
  const [adviceModel, setAdviceModel]     = useLocalStorage('shared.adviceModel', '')
  const [cleaningModel, setCleaningModel] = useLocalStorage('shared.cleaningModel', '')
  const [noReview, setNoReviewRaw] = useLocalStorage('dashboard.noReview', false)
  const [twoPass, setTwoPassRaw] = useLocalStorage('dashboard.twoPass', false)
  // Mutually exclusive: enabling one auto-disables the other. Two-pass review
  // is meaningless when entity review is skipped, so they can't both be true.
  const setNoReview = (v) => { setNoReviewRaw(v); if (v) setTwoPassRaw(false) }
  const setTwoPass = (v) => { setTwoPassRaw(v); if (v) setNoReviewRaw(false) }
  const [noClean, setNoClean] = useLocalStorage('dashboard.noClean', false)
  const [noStream, setNoStream] = useLocalStorage('dashboard.noStream', false)
  const [saveAsDraft, setSaveAsDraft] = useLocalStorage('dashboard.saveAsDraft', false)

  const [hideSynopses, setHideSynopses] = useLocalStorage('dashboard.hideSynopses', false)

  // Job state is shared, keyed by book — several can run at once. Prompts are
  // rendered globally by PromptHost, so this page no longer owns modal slots.
  const {
    isBookRunning, atCapacity, markStarted, maxConcurrent,
    active: activeJobsList, refresh: refreshJobs,
  } = useJobs()
  const activeCount = activeJobsList.length

  const logRef = useRef(null)

  // Books + providers + activity log + job status as queries. Failures
  // surface as a retryable banner instead of a silently empty workspace.
  const booksQuery = useQuery({ queryKey: ['books', 'minimal'], queryFn: () => api.listBooksMinimal() })
  const providersQuery = useQuery({ queryKey: ['providers'], queryFn: () => api.listProviders() })
  const activityLogQuery = useQuery({ queryKey: ['activity-log'], queryFn: () => api.getActivityLog() })

  const books = booksQuery.data?.books || []
  const providers = providersQuery.data?.providers || []
  const activityLogData = activityLogQuery.data
  const activityLog = useMemo(() => activityLogData?.entries || [], [activityLogData])

  const initialQueries = [booksQuery, providersQuery, activityLogQuery]
  const loadErrorObj = initialQueries.find(q => q.error)?.error
  const loadError = loadErrorObj ? (loadErrorObj.message || 'Request failed') : null
  const loadInitial = () => initialQueries.forEach(q => q.refetch())

  // Handle WebSocket messages — every message is delivered via the WS fan-out
  // (useWsEvent), so nothing is lost to React 18 batching. Missed events are
  // replayed by the backend on connect (flagged `replayed: true`). The backend
  // does NOT replay activity_log/progress — the ws_reconnected catch-up below
  // re-syncs those from the REST API instead.
  // This page now only reacts to activity-log traffic and to its OWN book
  // finishing. Everything else about job state lives in useJobs, keyed by book.
  useWsEvent((msg) => {
    const { type } = msg

    if (type === 'ws_reconnected') {
      queryClient.invalidateQueries({ queryKey: ['activity-log'] })
      return
    }

    if (type === 'translation_complete' || type === 'error') {
      queryClient.invalidateQueries({ queryKey: ['activity-log'] })
    }

    // Auto-advance to the next chapter so the paste → translate → paste loop
    // doesn't require manually bumping the number. Gated on this composer's
    // own book: any other book's completion (a queue worker draining book 79
    // while you stage book 14) would otherwise rewrite the field — and it is
    // persisted to localStorage, so the wrong number outlives the session.
    // The replay guard stays as belt-and-braces: the backend re-sends the
    // newest terminal event on every socket accept.
    if (type === 'translation_complete' && !msg.replayed
        && Number.isFinite(msg.chapter)
        && String(msg.book_id ?? '') === String(selectedBook ?? '')) {
      setChapterNum(String(msg.chapter + 1))
    }

    // Append activity log entries from the backend straight into the query
    // cache (dedup by id, so a re-synced full-log fetch racing an incoming
    // entry can't double-append)
    if (type === 'activity_log' && msg.entry) {
      queryClient.setQueryData(['activity-log'], (old) => {
        const entries = old?.entries || []
        if (entries.some(e => e.id === msg.entry.id)) return old
        return { ...(old || {}), entries: [...entries, msg.entry] }
      })
    }
  })

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [activityLog])

  const bookIdValue = selectedBook ? parseInt(selectedBook) : null
  const thisBookRunning = isBookRunning(bookIdValue)
  const [startError, setStartError] = useState(null)

  const handleTranslate = async () => {
    if (!inputText.trim()) return
    setStartError(null)
    setQueueMsg(null)
    markStarted(bookIdValue)
    try {
      await api.translate({
        text: inputText,
        book_id: bookIdValue,
        chapter_number: chapterNum ? parseInt(chapterNum) : null,
        model: modelOverride || null,
        advice_model: adviceModel || null,
        cleaning_model: cleaningModel || null,
        no_review: noReview,
        two_pass: twoPass,
        no_clean: noClean,
        no_stream: noStream,
        save_as_draft: saveAsDraft,
      })
    } catch (e) {
      // A refusal (this book is busy, or all slots are in use) is normal now,
      // so show why inline rather than painting the whole page as errored.
      setStartError(e?.message || 'Could not start translation.')
    } finally {
      refreshJobs()
    }
  }

  // Queue instead of translating now. Same composer state, different sink:
  // the chapter is parked in the queue and picked up by a queue worker later.
  // A book is required — the queue is keyed by book, unlike a one-off
  // translation which can run with "No book / Default".
  const [queuing, setQueuing] = useState(false)
  const [queueMsg, setQueueMsg] = useState(null)

  const handleAddToQueue = async () => {
    if (!inputText.trim() || !bookIdValue) return
    setStartError(null)
    setQueueMsg(null)
    setQueuing(true)
    try {
      const res = await api.addToQueue({
        text: inputText,
        book_id: bookIdValue,
        chapter_number: chapterNum ? parseInt(chapterNum) : null,
      })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      // Advance the composer the way a finished translation does, so the
      // paste → queue → paste loop doesn't need the number re-typed.
      if (chapterNum) setChapterNum(String(parseInt(chapterNum) + 1))
      setInputText('')
      setQueueMsg(`Added to queue${res?.count ? ` — ${res.count} item${res.count === 1 ? '' : 's'} waiting` : ''}.`)
    } catch (e) {
      setStartError(e?.message || 'Could not add to queue.')
    } finally {
      setQueuing(false)
    }
  }

  const handleCancel = async (bookId) => {
    try { await api.cancelJob(bookId) } catch { /* ignore */ }
    refreshJobs()
  }

  const handleStopAuto = async (bookId) => {
    try { await api.stopAutoProcess(bookId) } catch { /* ignore */ }
    refreshJobs()
  }

  const clearLog = async () => {
    try { await api.clearActivityLog() } catch { /* ignore */ }
    queryClient.setQueryData(['activity-log'], { entries: [] })
  }

  // Build model list from providers
  const modelOptions = providers.flatMap(p =>
    (p.models || []).map(m => `${p.name}:${m}`)
  )


  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-800 bg-slate-900/50 shrink-0">
        <h1 className="text-sm font-semibold text-slate-300">Translation Workspace</h1>
        <span className="text-xs text-slate-500">
          {activeCount ? `${activeCount} of ${maxConcurrent} translating` : 'Idle'}
        </span>
      </div>

      {/* Initial-load failure banner */}
      {loadError && (
        <div className="px-5 py-3 shrink-0">
          <ErrorState
            message="Failed to load workspace data"
            detail={loadError}
            onRetry={loadInitial}
          />
        </div>
      )}

      {/* Main split */}
      <div className="flex flex-col md:flex-row flex-1 overflow-hidden">
        {/* Left: input panel */}
        <div className="w-full md:w-[420px] shrink-0 flex flex-col border-b md:border-b-0 md:border-r border-slate-800 bg-slate-900/30">
          {/* Controls */}
          <div className="p-4 space-y-3 border-b border-slate-800">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {/* Book selector */}
              <div>
                <label className="label">Book</label>
                <select
                  className="input"
                  value={selectedBook}
                  onChange={e => setSelectedBook(e.target.value)}
                >
                  <option value="">No book / Default</option>
                  {books.map(b => (
                    <option key={b.id} value={b.id}>{b.id}: {b.title}</option>
                  ))}
                </select>
              </div>
              {/* Chapter */}
              <div>
                <label className="label">Chapter #</label>
                <input
                  className="input"
                  type="number"
                  min="1"
                  placeholder="auto"
                  value={chapterNum}
                  onChange={e => setChapterNum(e.target.value)}
                />
              </div>
            </div>

            {/* Model overrides */}
            <div>
              <label className="label">Translation model</label>
              <ComboBox
                value={modelOverride}
                onChange={setModelOverride}
                options={modelOptions}
                placeholder="Default"
              />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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

            {/* Options */}
            <div className="flex flex-col gap-1.5">
              <label
                className={`flex items-center gap-2 text-sm select-none ${twoPass ? 'text-slate-500 cursor-not-allowed' : 'text-slate-300 cursor-pointer'}`}
                title={twoPass ? 'Disabled because Two-pass is on' : ''}
              >
                <input
                  type="checkbox"
                  className="rounded border-slate-600"
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
                  className="rounded border-slate-600"
                  checked={twoPass}
                  disabled={noReview}
                  onChange={e => setTwoPass(e.target.checked)}
                />
                Two-pass — review entities before translating
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
                  className="rounded border-slate-600"
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
                  className="rounded border-slate-600"
                  checked={saveAsDraft}
                  onChange={e => setSaveAsDraft(e.target.checked)}
                />
                Save as draft
                <span className="relative group">
                  <Info size={13} className="text-slate-500 hover:text-slate-300 cursor-help" />
                  <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-64 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                    The translated chapter is saved unpublished — invisible on the public site until you publish it (from the chapter editor or the Books page). Default is to publish immediately.
                  </span>
                </span>
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
                <input
                  type="checkbox"
                  className="rounded border-slate-600"
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
            </div>

            {/* Action buttons */}
            <div className="flex gap-2">
              {/* Stays "Translate" even while other books run — each job has
                  its own Cancel on its card. Only this book being busy, or
                  every slot being taken, blocks a new start. */}
              <button
                className="btn-primary flex items-center gap-1.5 flex-1"
                onClick={handleTranslate}
                disabled={!inputText.trim() || thisBookRunning || atCapacity}
                title={
                  thisBookRunning ? 'This book is already translating'
                    : atCapacity ? `All ${maxConcurrent} translation slots are in use`
                    : undefined
                }
              >
                <Play size={13} /> Translate
              </button>
              {/* Queuing is always allowed — it starts nothing, so a busy book
                  or a full slot roster is irrelevant. It does need a book. */}
              <button
                className="btn-secondary flex items-center gap-1.5"
                onClick={handleAddToQueue}
                disabled={!inputText.trim() || !bookIdValue || queuing}
                title={!bookIdValue ? 'Select a book to queue a chapter' : 'Add this chapter to the queue'}
              >
                <ListPlus size={13} /> {queuing ? 'Queuing…' : 'Add to queue'}
              </button>
            </div>
            {queueMsg && (
              <p className="text-xs text-emerald-400 mt-1.5">
                {queueMsg} <Link to="/queue" className="underline hover:text-emerald-300">View queue</Link>
              </p>
            )}
            {(thisBookRunning || atCapacity || startError) && (
              <p className="text-xs text-amber-400 mt-1.5">
                {startError
                  || (thisBookRunning
                    ? 'This book is already translating — its chapters run in order.'
                    : `All ${maxConcurrent} translation slots are in use.`)}
              </p>
            )}
          </div>

          {/* Text input */}
          <div className="flex-1 flex flex-col p-4 gap-2 min-h-0 max-h-[40vh] md:max-h-none">
            <label className="label">Chinese source text</label>
            <textarea
              className="input flex-1 resize-none font-mono text-xs leading-relaxed"
              placeholder="Paste Chinese text here…"
              value={inputText}
              onChange={e => setInputText(e.target.value)}
            />
            <p className="text-xs text-slate-600 text-right">
              {inputText.length.toLocaleString()} chars
            </p>
          </div>
        </div>

        {/* Right: activity log */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Header */}
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-800 shrink-0">
            <span className="text-sm font-medium text-slate-300 flex-1">Activity Log</span>
            <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none">
              <input
                type="checkbox"
                className="rounded border-slate-600"
                checked={hideSynopses}
                onChange={e => setHideSynopses(e.target.checked)}
              />
              Hide synopses
            </label>
            {activityLog.length > 0 && (
              <button className="btn-ghost p-1.5" onClick={clearLog} title="Clear log">
                <Trash2 size={14} />
              </button>
            )}
          </div>

          {/* Progress banner — visible while running */}
          {activeCount > 0 && (
            <div className="px-4 py-3 border-b border-indigo-900 bg-indigo-950/40 shrink-0">
              <JobList onCancel={handleCancel} onStopAuto={handleStopAuto} />
            </div>
          )}

          {/* Log content */}
          <div ref={logRef} className="flex-1 overflow-y-auto p-4">
            {activityLog.length > 0 ? (
              <div className="space-y-1.5">
                {activityLog
                  .filter(e => !hideSynopses || !/^Synopsis:/i.test(e.message))
                  .map((entry, i) => (
                  <ActivityEntry key={entry.id || i} entry={entry} />
                ))}
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-slate-600 text-sm">
                Translation activity will appear here
              </div>
            )}
          </div>
        </div>
      </div>

    </div>
  )
}

function ActivityEntry({ entry }) {
  const time = new Date(entry.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  const styleMap = {
    start:             'text-indigo-400',
    complete:          'text-emerald-400',
    error:             'text-rose-400',
    warning:           'text-amber-400',
    info:              'text-slate-400',
    entity_review:     'text-amber-400',
    json_fix:          'text-amber-400',
    entities_accepted: 'text-slate-300',
    entity_edited:     'text-amber-300',
    entity_deleted:    'text-rose-300',
    entity_cleaned:    'text-slate-400',
  }

  return (
    <div className={`text-xs leading-relaxed ${styleMap[entry.type] || 'text-slate-400'}`}>
      <span className="text-slate-600 mr-2">{time}</span>
      <span>{entry.message}</span>
      {entry.entities?.map((e, i) => (
        <span key={i}>
          {i > 0 && ', '}
          {' '}
          <Link
            to={e.link || `/entities?search=${encodeURIComponent(e.name)}`}
            className="text-indigo-400 hover:text-indigo-300 underline underline-offset-2"
          >
            {e.label}
          </Link>
        </span>
      ))}
      {entry.type === 'complete' && entry.book_id && entry.chapter && (
        <Link
          to={`/books/${entry.book_id}/chapters/${entry.chapter}/edit`}
          className="ml-2 text-indigo-400 hover:text-indigo-300 underline underline-offset-2"
        >
          proofread
        </Link>
      )}
    </div>
  )
}

