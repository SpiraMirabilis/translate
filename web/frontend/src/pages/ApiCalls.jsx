import { useState, useEffect, useCallback, lazy, Suspense } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { api } from '../services/api'
import {
  ChevronDown, ChevronRight, ArrowLeft, Clock, Loader2, Save, Check,
  AlertTriangle, Cpu, Hash
} from 'lucide-react'

const CodeEditor = lazy(() => import('@uiw/react-textarea-code-editor'))

export default function ApiCalls() {
  const { bookId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const chapterFilter = searchParams.get('chapter') != null ? Number(searchParams.get('chapter')) : null

  const [book, setBook] = useState(null)
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [expandedSessions, setExpandedSessions] = useState(new Set())
  const [expandedPrompts, setExpandedPrompts] = useState(new Set())
  const [expandedSource, setExpandedSource] = useState(new Set())
  const [editingCall, setEditingCall] = useState(null)
  const [editedText, setEditedText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [chapters, setChapters] = useState([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [bookData, callsData, chaptersData] = await Promise.all([
        api.getBook(bookId),
        api.listApiCalls(bookId, chapterFilter),
        api.listChapters(bookId),
      ])
      setBook(bookData)
      setSessions(callsData.sessions || [])
      // Build unique chapter numbers from sessions for the filter dropdown
      const chList = (chaptersData.chapters || []).map(c => c.chapter_number)
      setChapters([...new Set(chList)].sort((a, b) => a - b))
    } catch (e) {
      console.error('Failed to load API calls:', e)
    }
    setLoading(false)
  }, [bookId, chapterFilter])

  useEffect(() => { load() }, [load])

  const toggleSession = (sid) => {
    setExpandedSessions(prev => {
      const next = new Set(prev)
      next.has(sid) ? next.delete(sid) : next.add(sid)
      return next
    })
  }

  const togglePrompt = (callId) => {
    setExpandedPrompts(prev => {
      const next = new Set(prev)
      next.has(callId) ? next.delete(callId) : next.add(callId)
      return next
    })
  }

  const toggleSource = (callId) => {
    setExpandedSource(prev => {
      const next = new Set(prev)
      next.has(callId) ? next.delete(callId) : next.add(callId)
      return next
    })
  }

  const startEdit = (call) => {
    setEditingCall(call.id)
    setEditedText(call.response_text || '')
    setSaved(false)
  }

  const saveEdit = async () => {
    setSaving(true)
    try {
      await api.updateApiCall(editingCall, { response_text: editedText })
      setSaved(true)
      // Update local state
      setSessions(prev => prev.map(s => ({
        ...s,
        calls: s.calls.map(c => c.id === editingCall ? { ...c, response_text: editedText } : c)
      })))
      setTimeout(() => { setEditingCall(null); setSaved(false) }, 800)
    } catch (e) {
      console.error('Failed to save:', e)
    }
    setSaving(false)
  }

  const cancelEdit = () => {
    setEditingCall(null)
    setEditedText('')
    setSaved(false)
  }

  const formatDuration = (ms) => {
    if (ms < 1000) return `${ms}ms`
    return `${(ms / 1000).toFixed(1)}s`
  }

  const formatDate = (iso) => {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        <Loader2 className="animate-spin mr-2" size={18} /> Loading API calls...
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link to="/books" className="text-slate-400 hover:text-slate-200">
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="text-lg font-semibold text-slate-100">API Call Logs</h1>
          {book && <p className="text-sm text-slate-400">{book.title}</p>}
        </div>
      </div>

      {/* Chapter filter */}
      {chapters.length > 0 && (
        <div className="flex items-center gap-2 mb-4">
          <label className="text-xs text-slate-400">Filter by chapter:</label>
          <select
            className="input text-xs py-1 px-2 w-32"
            value={chapterFilter ?? ''}
            onChange={(e) => {
              const v = e.target.value
              if (v === '') {
                searchParams.delete('chapter')
              } else {
                searchParams.set('chapter', v)
              }
              setSearchParams(searchParams)
            }}
          >
            <option value="">All chapters</option>
            {chapters.map(n => (
              <option key={n} value={n}>Chapter {n}</option>
            ))}
          </select>
        </div>
      )}

      {/* Sessions */}
      {sessions.length === 0 ? (
        <div className="text-sm text-slate-500 mt-8 text-center">
          No API calls logged yet for this book.
        </div>
      ) : (
        <div className="space-y-2">
          {sessions.map(session => {
            const isExpanded = expandedSessions.has(session.session_id)
            const totalTokens = session.calls.reduce((s, c) => s + (c.total_tokens || c.completion_tokens || 0), 0)
            const totalDuration = session.calls.reduce((s, c) => s + (c.duration_ms || 0), 0)
            const hasFailures = session.calls.some(c => !c.success)
            return (
              <div key={session.session_id} className="border border-slate-700 rounded-lg overflow-hidden">
                {/* Session header */}
                <button
                  className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/50 transition-colors"
                  onClick={() => toggleSession(session.session_id)}
                >
                  {isExpanded ? <ChevronDown size={14} className="text-slate-400 shrink-0" /> : <ChevronRight size={14} className="text-slate-400 shrink-0" />}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-slate-200">
                        Chapter {session.chapter_number ?? '?'}
                      </span>
                      <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">
                        {session.total_chunks} chunk{session.total_chunks !== 1 ? 's' : ''}
                      </span>
                      {hasFailures && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-rose-900/50 text-rose-300 flex items-center gap-1">
                          <AlertTriangle size={10} /> failures
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-slate-500">
                      <span className="flex items-center gap-1"><Cpu size={10} /> {session.model_name}</span>
                      <span className="flex items-center gap-1"><Clock size={10} /> {formatDate(session.created_at)}</span>
                      {totalTokens > 0 && <span>{totalTokens.toLocaleString()} tokens</span>}
                      <span>{formatDuration(totalDuration)}</span>
                    </div>
                  </div>
                </button>

                {/* Expanded: chunk cards */}
                {isExpanded && (
                  <div className="border-t border-slate-700 divide-y divide-slate-800">
                    {session.calls.map(call => (
                      <div key={call.id} className="px-4 py-3">
                        {/* Chunk meta bar */}
                        <div className="flex items-center gap-2 flex-wrap text-xs mb-2">
                          <span className="flex items-center gap-1 text-slate-300 font-medium">
                            <Hash size={11} /> Chunk {call.chunk_index}/{call.total_chunks}
                          </span>
                          {call.attempt > 0 && (
                            <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-300">
                              retry #{call.attempt}
                            </span>
                          )}
                          <span className={`px-1.5 py-0.5 rounded ${call.success ? 'bg-emerald-900/40 text-emerald-300' : 'bg-rose-900/40 text-rose-300'}`}>
                            {call.success ? 'success' : 'failed'}
                          </span>
                          {(call.total_tokens > 0 || call.completion_tokens > 0) && (
                            <span className="text-slate-500">
                              {call.prompt_tokens > 0
                                ? `${call.prompt_tokens.toLocaleString()} + ${call.completion_tokens.toLocaleString()} tokens`
                                : `~${call.completion_tokens.toLocaleString()} tokens (est)`}
                            </span>
                          )}
                          <span className="text-slate-500">{formatDuration(call.duration_ms)}</span>
                        </div>

                        {/* System prompt (collapsible) */}
                        <div className="mb-1">
                          <button
                            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
                            onClick={() => togglePrompt(call.id)}
                          >
                            {expandedPrompts.has(call.id) ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                            System Prompt
                          </button>
                          {expandedPrompts.has(call.id) && (
                            <pre className="mt-1 p-3 rounded bg-slate-950 border border-slate-800 text-xs text-slate-400 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-words">
                              {call.system_prompt || '(empty)'}
                            </pre>
                          )}
                        </div>

                        {/* User prompt / source text (collapsible) */}
                        <div className="mb-2">
                          <button
                            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
                            onClick={() => toggleSource(call.id)}
                          >
                            {expandedSource.has(call.id) ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                            Source Text
                          </button>
                          {expandedSource.has(call.id) && (
                            <pre className="mt-1 p-3 rounded bg-slate-950 border border-slate-800 text-xs text-slate-400 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-words">
                              {call.user_prompt || '(empty)'}
                            </pre>
                          )}
                        </div>

                        {/* Response (always visible, editable) */}
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs text-slate-500">Response</span>
                            {editingCall !== call.id ? (
                              <button
                                className="text-xs text-indigo-400 hover:text-indigo-300"
                                onClick={() => startEdit(call)}
                              >
                                Edit
                              </button>
                            ) : (
                              <div className="flex items-center gap-2">
                                <button
                                  className="text-xs text-slate-400 hover:text-slate-200"
                                  onClick={cancelEdit}
                                  disabled={saving}
                                >
                                  Cancel
                                </button>
                                <button
                                  className="text-xs text-emerald-400 hover:text-emerald-300 flex items-center gap-1 disabled:opacity-50"
                                  onClick={saveEdit}
                                  disabled={saving}
                                >
                                  {saving ? <Loader2 size={11} className="animate-spin" /> : saved ? <Check size={11} /> : <Save size={11} />}
                                  {saving ? 'Saving...' : saved ? 'Saved' : 'Save'}
                                </button>
                              </div>
                            )}
                          </div>
                          {editingCall === call.id ? (
                            <div className="rounded-lg overflow-hidden border border-slate-700">
                              <Suspense fallback={<div className="p-4 text-slate-400 text-sm">Loading editor...</div>}>
                                <CodeEditor
                                  value={editedText}
                                  language="json"
                                  onChange={(e) => setEditedText(e.target.value)}
                                  padding={16}
                                  style={{
                                    fontSize: 13,
                                    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
                                    backgroundColor: '#0f172a',
                                    minHeight: 200,
                                    maxHeight: 500,
                                    overflow: 'auto',
                                  }}
                                  data-color-mode="dark"
                                />
                              </Suspense>
                            </div>
                          ) : (
                            <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs text-slate-300 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap break-words">
                              {call.response_text || '(empty)'}
                            </pre>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
