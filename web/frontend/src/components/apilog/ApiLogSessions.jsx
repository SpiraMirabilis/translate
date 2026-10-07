import { useState, lazy, Suspense } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../services/api'
import { useTransientFlag } from '../../hooks/useTransientFlag'
import {
  ChevronDown, ChevronRight, Clock, Loader2, Save, Check,
  AlertTriangle, Cpu, Hash, BookOpen
} from 'lucide-react'

const JsonCodeMirror = lazy(() => import('../JsonCodeMirror'))

const formatDuration = (ms) => {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

const formatDate = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

const callTokens = (c) => c.total_tokens || c.completion_tokens || 0

/**
 * Paged list of API-call sessions, shared by the global API Logs page and the
 * per-book one. The list carries metadata only; a session's prompts and
 * responses are fetched when it is expanded (they run to tens of KB per call,
 * and loading them for every call made the page pull ~60 MB).
 */
export default function ApiLogSessions({ bookId = null, chapterNumber = null, showBook = false, emptyText }) {
  const listQuery = useInfiniteQuery({
    queryKey: ['api-calls', 'list', bookId, chapterNumber],
    queryFn: ({ pageParam }) => api.listApiCallSessions({ bookId, chapterNumber, before: pageParam }),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_before ?? undefined,
  })
  const [expanded, setExpanded] = useState(new Set())

  const sessions = (listQuery.data?.pages || []).flatMap(p => p.sessions || [])
  const totalCalls = sessions.reduce((s, sess) => s + sess.calls.length, 0)
  const totalTokens = sessions.reduce((s, sess) => s + sess.calls.reduce((t, c) => t + callTokens(c), 0), 0)
  const totalFailures = sessions.reduce((s, sess) => s + sess.calls.filter(c => !c.success).length, 0)

  const toggle = (sid) => setExpanded(prev => {
    const next = new Set(prev)
    next.has(sid) ? next.delete(sid) : next.add(sid)
    return next
  })

  if (listQuery.isPending) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        <Loader2 className="animate-spin mr-2" size={18} /> Loading API logs...
      </div>
    )
  }
  if (listQuery.isError) {
    return <div className="text-sm text-rose-400 mt-8 text-center">Failed to load API logs: {listQuery.error?.message}</div>
  }
  if (sessions.length === 0) {
    return <div className="text-sm text-slate-500 mt-8 text-center">{emptyText || 'No API calls logged yet.'}</div>
  }

  return (
    <div>
      <p className="text-xs text-slate-500 mb-3">
        Showing {sessions.length} session{sessions.length !== 1 ? 's' : ''} &middot; {totalCalls} call{totalCalls !== 1 ? 's' : ''}
        {totalTokens > 0 && <> &middot; {totalTokens.toLocaleString()} tokens</>}
        {totalFailures > 0 && <> &middot; <span className="text-rose-400">{totalFailures} failed</span></>}
      </p>

      <div className="space-y-2">
        {sessions.map(session => {
          const isExpanded = expanded.has(session.session_id)
          const sessionTokens = session.calls.reduce((s, c) => s + callTokens(c), 0)
          const totalDuration = session.calls.reduce((s, c) => s + (c.duration_ms || 0), 0)
          const hasFailures = session.calls.some(c => !c.success)
          return (
            <div key={session.session_id} className="border border-slate-700 rounded-lg overflow-hidden">
              <button
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/50 transition-colors"
                onClick={() => toggle(session.session_id)}
              >
                {isExpanded ? <ChevronDown size={14} className="text-slate-400 shrink-0" /> : <ChevronRight size={14} className="text-slate-400 shrink-0" />}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    {showBook && session.book_title && (
                      <Link
                        to={`/books/${session.book_id}/api-calls`}
                        className="text-xs px-1.5 py-0.5 rounded bg-indigo-900/40 text-indigo-300 hover:bg-indigo-900/60 flex items-center gap-1"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <BookOpen size={10} /> {session.book_title}
                      </Link>
                    )}
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
                    {sessionTokens > 0 && <span>{sessionTokens.toLocaleString()} tokens</span>}
                    <span>{formatDuration(totalDuration)}</span>
                  </div>
                </div>
              </button>
              {isExpanded && <SessionCalls sessionId={session.session_id} />}
            </div>
          )
        })}
      </div>

      {listQuery.hasNextPage && (
        <div className="flex justify-center mt-4">
          <button
            className="btn-ghost text-xs px-3 py-1.5 flex items-center gap-1.5"
            onClick={() => listQuery.fetchNextPage()}
            disabled={listQuery.isFetchingNextPage}
          >
            {listQuery.isFetchingNextPage && <Loader2 size={12} className="animate-spin" />}
            {listQuery.isFetchingNextPage ? 'Loading...' : 'Load more'}
          </button>
        </div>
      )}
    </div>
  )
}

function SessionCalls({ sessionId }) {
  const queryClient = useQueryClient()
  const sessionKey = ['api-calls', 'session', sessionId]
  const sessionQuery = useQuery({
    queryKey: sessionKey,
    queryFn: () => api.getApiCallSession(sessionId),
  })
  const [expandedPrompts, setExpandedPrompts] = useState(new Set())
  const [expandedSource, setExpandedSource] = useState(new Set())
  const [editingCall, setEditingCall] = useState(null)
  const [editedText, setEditedText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [saved, flashSaved, clearSaved] = useTransientFlag(800)

  const toggleIn = (setter) => (callId) => setter(prev => {
    const next = new Set(prev)
    next.has(callId) ? next.delete(callId) : next.add(callId)
    return next
  })
  const togglePrompt = toggleIn(setExpandedPrompts)
  const toggleSource = toggleIn(setExpandedSource)

  const startEdit = (call) => {
    setEditingCall(call.id)
    setEditedText(call.response_text || '')
    setSaveError(null)
    clearSaved()
  }

  const saveEdit = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      await api.updateApiCall(editingCall, { response_text: editedText })
      flashSaved()
      queryClient.setQueryData(sessionKey, prev => prev ? {
        ...prev,
        calls: prev.calls.map(c => c.id === editingCall ? { ...c, response_text: editedText } : c),
      } : prev)
      setTimeout(() => setEditingCall(null), 800)
    } catch (e) {
      console.error('Failed to save:', e)
      setSaveError(e.message || 'Save failed.')
    }
    setSaving(false)
  }

  const cancelEdit = () => {
    setEditingCall(null)
    setEditedText('')
    setSaveError(null)
    clearSaved()
  }

  if (sessionQuery.isPending) {
    return (
      <div className="border-t border-slate-700 px-4 py-3 text-xs text-slate-400 flex items-center gap-2">
        <Loader2 size={12} className="animate-spin" /> Loading calls...
      </div>
    )
  }
  if (sessionQuery.isError) {
    return (
      <div className="border-t border-slate-700 px-4 py-3 text-xs text-rose-400">
        Failed to load calls: {sessionQuery.error?.message}
      </div>
    )
  }

  return (
    <div className="border-t border-slate-700 divide-y divide-slate-800">
      {sessionQuery.data.calls.map(call => (
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
              <div>
                <div className="rounded-lg overflow-hidden border border-slate-700">
                  <Suspense fallback={<div className="p-4 text-slate-400 text-sm">Loading editor...</div>}>
                    <JsonCodeMirror
                      value={editedText}
                      onChange={(val) => setEditedText(val)}
                      minHeight="200px"
                      maxHeight="500px"
                    />
                  </Suspense>
                </div>
                {saveError && (
                  <p className="text-rose-400 text-xs mt-1.5">Save failed: {saveError}</p>
                )}
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
  )
}
