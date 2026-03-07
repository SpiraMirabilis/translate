/**
 * Dashboard — main translation workspace.
 *
 * Left panel:  input, book/chapter selector, model override, translate button
 * Right panel: streaming output + status log
 * Bottom:      entity review panel (modal overlay when entities need review)
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useWs } from '../App'
import { api } from '../services/api'
import EntityReviewPanel from '../components/EntityReviewPanel'
import TranslationProgress from '../components/TranslationProgress'
import ComboBox from '../components/ComboBox'
import { useLocalStorage } from '../hooks/useLocalStorage'
import {
  Play, Square, Copy, Loader2, Info
} from 'lucide-react'

export default function Dashboard() {
  const { lastMessage } = useWs()

  const [books, setBooks] = useState([])
  const [providers, setProviders] = useState([])
  const [inputText, setInputText] = useState('')
  const [selectedBook, setSelectedBook] = useState('')
  const [chapterNum, setChapterNum] = useState('')
  const [modelOverride, setModelOverride] = useState('')
  const [noReview, setNoReview] = useState(false)
  const [noClean, setNoClean] = useState(false)

  const [jobStatus, setJobStatus] = useState('idle')   // idle | running | awaiting_review | complete | error
  const [log, setLog] = useState([])                    // progress messages
  const [chunkProgress, setChunkProgress] = useState(null)  // latest progress payload
  const [output, setOutput] = useLocalStorage('dashboard.output', [])
  const [outputTitle, setOutputTitle] = useLocalStorage('dashboard.outputTitle', '')
  const [entityReview, setEntityReview] = useState(null) // { entities, context } or null

  const outputRef = useRef(null)
  const addLog = useCallback((msg, type = 'info') => {
    setLog(prev => [...prev, { msg, type, ts: Date.now() }])
  }, [])

  // Load books + providers on mount
  useEffect(() => {
    api.listBooks().then(d => setBooks(d.books || [])).catch(() => {})
    api.listProviders().then(d => setProviders(d.providers || [])).catch(() => {})
  }, [])

  // Handle WebSocket messages
  useEffect(() => {
    if (!lastMessage) return
    const { type } = lastMessage

    if (type === 'progress') {
      setChunkProgress(lastMessage)
      setJobStatus('running')
      if (lastMessage.phase === 'start') {
        addLog(`Translating chunk ${lastMessage.chunk} of ${lastMessage.total}…`, 'progress')
      }
    }

    if (type === 'entity_review_needed') {
      addLog('New entities found — review required.', 'warning')
      setJobStatus('awaiting_review')
      setEntityReview({ entities: lastMessage.entities, context: lastMessage.context })
    }

    if (type === 'translation_complete') {
      setOutput(lastMessage.content || [])
      setOutputTitle(lastMessage.title || '')
      setJobStatus('complete')
      setChunkProgress(null)
      addLog(`Translation complete: "${lastMessage.title}" (ch. ${lastMessage.chapter})`, 'success')
      setEntityReview(null)
    }

    if (type === 'error') {
      setJobStatus('error')
      addLog(`Error: ${lastMessage.message}`, 'error')
      setEntityReview(null)
    }
  }, [lastMessage, addLog])

  // Auto-scroll output
  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output, log])

  const handleTranslate = async () => {
    if (!inputText.trim()) return
    setLog([])
    setOutput([])        // also clears localStorage via useLocalStorage
    setOutputTitle('')
    setEntityReview(null)
    setChunkProgress(null)
    setJobStatus('running')
    addLog('Starting translation…', 'info')
    try {
      await api.translate({
        text: inputText,
        book_id: selectedBook ? parseInt(selectedBook) : null,
        chapter_number: chapterNum ? parseInt(chapterNum) : null,
        model: modelOverride || null,
        no_review: noReview,
        no_clean: noClean,
      })
    } catch (e) {
      setJobStatus('error')
      addLog(`Failed to start: ${e.message}`, 'error')
    }
  }

  const handleCancel = async () => {
    try { await api.cancelJob() } catch { /* ignore */ }
    setJobStatus('idle')
    addLog('Cancelled.', 'info')
  }

  const handleReviewDone = () => {
    setEntityReview(null)
    setJobStatus('running')
    addLog('Review submitted — resuming translation…', 'info')
  }

  const copyOutput = () => {
    navigator.clipboard.writeText(output.join('\n'))
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

            {/* Model override */}
            <div>
              <label className="label">Model override (optional)</label>
              <ComboBox
                value={modelOverride}
                onChange={setModelOverride}
                options={modelOptions}
                placeholder="Use default"
              />
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

        {/* Right: output panel */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Output header */}
          <div className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-800 shrink-0">
            <span className="text-sm font-medium text-slate-300 flex-1 truncate">
              {outputTitle || 'Translation output'}
            </span>
            {output.length > 0 && (
              <button className="btn-ghost p-1.5" onClick={copyOutput} title="Copy to clipboard">
                <Copy size={14} />
              </button>
            )}
          </div>

          {/* Progress banner — visible while running regardless of output state */}
          {isRunning && (
            <div className="px-4 py-3 border-b border-indigo-900 bg-indigo-950/40 shrink-0">
              <TranslationProgress progress={chunkProgress} status={jobStatus} />
            </div>
          )}

          {/* Output content */}
          <div ref={outputRef} className="flex-1 overflow-y-auto p-4">
            {output.length > 0 ? (
              <pre className="text-sm text-slate-200 font-mono whitespace-pre-wrap leading-relaxed">
                {output.join('\n')}
              </pre>
            ) : (
              <div className="h-full flex flex-col gap-4">
                {/* Log lines */}
                <div className="space-y-1 flex-1">
                  {log.map((entry, i) => (
                    <LogLine key={i} entry={entry} />
                  ))}
                </div>
                {log.length === 0 && !isRunning && (
                  <div className="flex-1 flex items-center justify-center text-slate-600 text-sm">
                    Translation output will appear here
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Log strip at bottom when output is shown */}
          {output.length > 0 && log.length > 0 && (
            <div className="border-t border-slate-800 px-4 py-2 max-h-24 overflow-y-auto shrink-0">
              {log.slice(-5).map((entry, i) => (
                <LogLine key={i} entry={entry} compact />
              ))}
            </div>
          )}
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

function LogLine({ entry, compact = false }) {
  const colorMap = {
    info:     'text-slate-400',
    progress: 'text-indigo-400',
    warning:  'text-amber-400',
    success:  'text-emerald-400',
    error:    'text-rose-400',
  }
  return (
    <div className={`${colorMap[entry.type] || 'text-slate-400'} ${compact ? 'text-xs' : 'text-xs'} leading-relaxed`}>
      {entry.msg}
    </div>
  )
}
