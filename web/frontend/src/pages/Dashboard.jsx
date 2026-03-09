/**
 * Dashboard — main translation workspace.
 *
 * Left panel:  input, book/chapter selector, model override, translate button
 * Right panel: persistent activity log + progress
 * Bottom:      entity review panel (modal overlay when entities need review)
 */
import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useWs } from '../App'
import { api } from '../services/api'
import EntityReviewPanel from '../components/EntityReviewPanel'
import TranslationProgress from '../components/TranslationProgress'
import ComboBox from '../components/ComboBox'
import { useLocalStorage } from '../hooks/useLocalStorage'
import {
  Play, Square, Info, Trash2
} from 'lucide-react'

export default function Dashboard() {
  const { lastMessage } = useWs()

  const [books, setBooks] = useState([])
  const [providers, setProviders] = useState([])
  const [inputText, setInputText] = useState('')
  const [selectedBook, setSelectedBook] = useState('')
  const [chapterNum, setChapterNum] = useState('')
  const [modelOverride, setModelOverride] = useState('')
  const [adviceModel, setAdviceModel]     = useLocalStorage('shared.adviceModel', '')
  const [cleaningModel, setCleaningModel] = useLocalStorage('shared.cleaningModel', '')
  const [noReview, setNoReview] = useState(false)
  const [noClean, setNoClean] = useState(false)
  const [noRepair, setNoRepair] = useState(false)

  const [jobStatus, setJobStatus] = useState('idle')   // idle | running | awaiting_review | complete | error
  const [chunkProgress, setChunkProgress] = useState(null)
  const [activityLog, setActivityLog] = useState([])
  const [entityReview, setEntityReview] = useState(null) // { entities, context } or null

  const logRef = useRef(null)

  // Load books + providers + activity log on mount
  useEffect(() => {
    api.listBooks().then(d => setBooks(d.books || [])).catch(() => {})
    api.listProviders().then(d => setProviders(d.providers || [])).catch(() => {})
    api.getActivityLog().then(d => setActivityLog(d.entries || [])).catch(() => {})
  }, [])

  // Handle WebSocket messages
  useEffect(() => {
    if (!lastMessage) return
    const { type } = lastMessage

    if (type === 'progress') {
      setChunkProgress(lastMessage)
      setJobStatus('running')
    }

    if (type === 'entity_review_needed') {
      setJobStatus('awaiting_review')
      setEntityReview({ entities: lastMessage.entities, context: lastMessage.context })
    }

    if (type === 'translation_complete') {
      setJobStatus('complete')
      setChunkProgress(null)
      setEntityReview(null)
    }

    if (type === 'error') {
      setJobStatus('error')
      setEntityReview(null)
    }

    // Append activity log entries from the backend
    if (type === 'activity_log' && lastMessage.entry) {
      setActivityLog(prev => {
        if (prev.some(e => e.id === lastMessage.entry.id)) return prev
        return [...prev, lastMessage.entry]
      })
    }
  }, [lastMessage])

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [activityLog])

  const handleTranslate = async () => {
    if (!inputText.trim()) return
    setEntityReview(null)
    setChunkProgress(null)
    setJobStatus('running')
    try {
      await api.translate({
        text: inputText,
        book_id: selectedBook ? parseInt(selectedBook) : null,
        chapter_number: chapterNum ? parseInt(chapterNum) : null,
        model: modelOverride || null,
        advice_model: adviceModel || null,
        cleaning_model: cleaningModel || null,
        no_review: noReview,
        no_clean: noClean,
        no_repair: noRepair,
      })
    } catch (e) {
      setJobStatus('error')
    }
  }

  const handleCancel = async () => {
    try { await api.cancelJob() } catch { /* ignore */ }
    setJobStatus('idle')
  }

  const handleReviewDone = () => {
    setEntityReview(null)
    setJobStatus('running')
  }

  const clearLog = async () => {
    try { await api.clearActivityLog() } catch { /* ignore */ }
    setActivityLog([])
  }

  // Build model list from providers
  const modelOptions = providers.flatMap(p =>
    (p.models || []).map(m => `${p.name}:${m}`)
  )

  const isRunning = jobStatus === 'running' || jobStatus === 'awaiting_review'

  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-800 bg-slate-900/50 shrink-0">
        <h1 className="text-sm font-semibold text-slate-300">Translation Workspace</h1>
        <StatusBadge status={jobStatus} />
      </div>

      {/* Main split */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: input panel */}
        <div className="w-[420px] shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/30">
          {/* Controls */}
          <div className="p-4 space-y-3 border-b border-slate-800">
            <div className="grid grid-cols-2 gap-3">
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
            <div className="grid grid-cols-2 gap-3">
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
              <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none">
                <input
                  type="checkbox"
                  className="rounded border-slate-600"
                  checked={noReview}
                  onChange={e => setNoReview(e.target.checked)}
                />
                Skip entity review
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
                  checked={noRepair}
                  onChange={e => setNoRepair(e.target.checked)}
                />
                Skip partial repair
                <span className="relative group">
                  <Info size={13} className="text-slate-500 hover:text-slate-300 cursor-help" />
                  <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-64 px-3 py-2 rounded bg-slate-700 text-xs text-slate-200 leading-relaxed opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50 shadow-lg">
                    After translation, lines still containing Chinese characters are automatically retranslated using the cleaning model. Disable this if you prefer to handle untranslated lines manually.
                  </span>
                </span>
              </label>
            </div>

            {/* Action buttons */}
            <div className="flex gap-2">
              {isRunning ? (
                <button className="btn-danger flex items-center gap-1.5 flex-1" onClick={handleCancel}>
                  <Square size={13} /> Cancel
                </button>
              ) : (
                <button
                  className="btn-primary flex items-center gap-1.5 flex-1"
                  onClick={handleTranslate}
                  disabled={!inputText.trim()}
                >
                  <Play size={13} /> Translate
                </button>
              )}
            </div>
          </div>

          {/* Text input */}
          <div className="flex-1 flex flex-col p-4 gap-2">
            <label className="label">Chinese source text</label>
            <textarea
              className="input flex-1 resize-none font-mono text-xs leading-relaxed"
              placeholder="Paste Chinese text here…"
              value={inputText}
              onChange={e => setInputText(e.target.value)}
              disabled={isRunning}
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
            {activityLog.length > 0 && (
              <button className="btn-ghost p-1.5" onClick={clearLog} title="Clear log">
                <Trash2 size={14} />
              </button>
            )}
          </div>

          {/* Progress banner — visible while running */}
          {isRunning && (
            <div className="px-4 py-3 border-b border-indigo-900 bg-indigo-950/40 shrink-0">
              <TranslationProgress progress={chunkProgress} status={jobStatus} />
            </div>
          )}

          {/* Log content */}
          <div ref={logRef} className="flex-1 overflow-y-auto p-4">
            {activityLog.length > 0 ? (
              <div className="space-y-1.5">
                {activityLog.map((entry, i) => (
                  <ActivityEntry key={i} entry={entry} />
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

      {/* Entity review overlay */}
      {entityReview && (
        <EntityReviewPanel
          entities={entityReview.entities}
          context={entityReview.context}
          onDone={handleReviewDone}
        />
      )}
    </div>
  )
}

function ActivityEntry({ entry }) {
  const time = new Date(entry.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  const styleMap = {
    start:             'text-indigo-400',
    complete:          'text-emerald-400',
    error:             'text-rose-400',
    info:              'text-slate-400',
    entity_review:     'text-amber-400',
    entities_accepted: 'text-slate-300',
    entity_edited:     'text-amber-300',
    entity_deleted:    'text-rose-300',
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
            to={`/entities?search=${encodeURIComponent(e.name)}`}
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

function StatusBadge({ status }) {
  const map = {
    idle:             { label: 'Idle',           cls: 'badge-slate'   },
    running:          { label: 'Translating…',   cls: 'badge-indigo'  },
    awaiting_review:  { label: 'Review needed',  cls: 'badge-amber'   },
    complete:         { label: 'Complete',        cls: 'badge-emerald' },
    error:            { label: 'Error',           cls: 'badge-rose'    },
  }
  const { label, cls } = map[status] || map.idle
  return <span className={cls}>{label}</span>
}
