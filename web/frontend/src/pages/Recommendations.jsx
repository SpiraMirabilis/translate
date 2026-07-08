import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MessageSquarePlus, Trash2, ExternalLink, ChevronDown, ChevronUp, Loader2, Mail, CornerDownRight, Inbox } from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'

const STATUS_OPTIONS = ['new', 'reviewed', 'accepted', 'dismissed']
const STATUS_COLORS = {
  new:       'bg-blue-500/20 text-blue-400',
  reviewed:  'bg-amber-500/20 text-amber-400',
  accepted:  'bg-emerald-500/20 text-emerald-400',
  dismissed: 'bg-slate-500/20 text-slate-400',
}
const LANG_LABELS = { zh: 'Chinese', ko: 'Korean', ja: 'Japanese' }

const TABS = [
  { value: null,        label: 'All' },
  { value: 'new',       label: 'New' },
  { value: 'reviewed',  label: 'Reviewed' },
  { value: 'accepted',  label: 'Accepted' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: 'unmatched', label: 'Unmatched replies' },
]

export default function Recommendations() {
  const { site_name } = useSite()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [editingNotes, setEditingNotes] = useState({})
  const [emailDraft, setEmailDraft] = useState({})   // id -> message text
  const [emailState, setEmailState] = useState({})   // id -> { sending, result }

  const isUnmatched = filter === 'unmatched'

  const { data, isPending: recsLoading } = useQuery({
    queryKey: ['recommendations', filter],
    queryFn: () => api.listRecommendations(filter),
    enabled: !isUnmatched,
  })
  const items = data?.items || []

  const { data: unmatchedData, isPending: unmatchedLoading } = useQuery({
    queryKey: ['recommendation-replies-unmatched'],
    queryFn: () => api.listUnmatchedReplies(),
    enabled: isUnmatched,
  })
  const unmatched = unmatchedData?.items || []
  const loading = isUnmatched ? unmatchedLoading : recsLoading

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['recommendations'] })

  useEffect(() => {
    document.title = `Recommendations | ${site_name}`
    return () => { document.title = site_name }
  }, [site_name])

  const handleStatusChange = async (id, newStatus) => {
    await api.updateRecommendation(id, { status: newStatus })
    invalidate()
  }

  const handleSaveNotes = async (id) => {
    const notes = editingNotes[id] ?? ''
    await api.updateRecommendation(id, { admin_notes: notes })
    invalidate()
  }

  const handleDelete = async (id) => {
    if (!confirm('Delete this recommendation?')) return
    await api.deleteRecommendation(id)
    invalidate()
  }

  const handleSendEmail = async (id) => {
    const message = (emailDraft[id] ?? '').trim()
    if (!message) return
    setEmailState(prev => ({ ...prev, [id]: { sending: true } }))
    try {
      const res = await api.emailRecommendation(id, message)
      const result = res.sent
        ? { ok: true, text: 'Email sent.' }
        : { ok: false, text: res.detail === 'suppressed'
            ? 'Not sent — this requester has unsubscribed.'
            : `Not sent (${res.detail || 'unknown error'}).` }
      setEmailState(prev => ({ ...prev, [id]: { sending: false, result } }))
      if (res.sent) setEmailDraft(prev => ({ ...prev, [id]: '' }))
    } catch (e) {
      setEmailState(prev => ({ ...prev, [id]: { sending: false, result: { ok: false, text: 'Request failed.' } } }))
    }
  }

  const toggleExpand = (id) => {
    if (expanded === id) {
      setExpanded(null)
    } else {
      setExpanded(id)
      const rec = items.find(r => r.id === id)
      if (rec && !(id in editingNotes)) {
        setEditingNotes(prev => ({ ...prev, [id]: rec.admin_notes || '' }))
      }
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <MessageSquarePlus size={24} className="text-indigo-400" />
        <h1 className="text-2xl font-bold text-slate-100">Recommendations</h1>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 mb-6 flex-wrap">
        {TABS.map(tab => (
          <button
            key={tab.label}
            onClick={() => setFilter(tab.value)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              filter === tab.value
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-400 hover:text-slate-200 hover:bg-slate-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* List */}
      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 size={28} className="animate-spin text-indigo-400" />
        </div>
      ) : isUnmatched ? (
        unmatched.length === 0 ? (
          <div className="text-center py-16 text-slate-500">
            <Inbox size={40} className="mx-auto mb-3 opacity-30" />
            <p>No unmatched replies</p>
            <p className="text-xs mt-1">Replies that couldn&apos;t be tied to a request would appear here.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {unmatched.map(r => <ReplyCard key={r.id} reply={r} showFrom />)}
          </div>
        )
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <MessageSquarePlus size={40} className="mx-auto mb-3 opacity-30" />
          <p>No recommendations{filter ? ` with status "${filter}"` : ''}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(rec => (
            <div key={rec.id} className="card border border-slate-700 rounded-lg overflow-hidden">
              {/* Row header */}
              <button
                onClick={() => toggleExpand(rec.id)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/50 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-slate-100 truncate">{rec.novel_title}</span>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[rec.status] || STATUS_COLORS.new}`}>
                      {rec.status}
                    </span>
                    {rec.source_language && (
                      <span className="text-xs text-slate-500">{LANG_LABELS[rec.source_language] || rec.source_language}</span>
                    )}
                    {rec.unread_reply_count > 0 && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-rose-500/20 text-rose-400">
                        <CornerDownRight size={11} /> {rec.unread_reply_count} new
                      </span>
                    )}
                    {rec.unread_reply_count === 0 && rec.reply_count > 0 && (
                      <span className="flex items-center gap-1 text-xs text-slate-500">
                        <CornerDownRight size={11} /> {rec.reply_count}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-slate-500 mt-1">
                    <span>{rec.requester_name}</span>
                    <span>{rec.requester_email}</span>
                    <span>{rec.created_at?.replace('T', ' ').slice(0, 16)}</span>
                  </div>
                </div>
                {expanded === rec.id ? <ChevronUp size={16} className="text-slate-500" /> : <ChevronDown size={16} className="text-slate-500" />}
              </button>

              {/* Expanded details */}
              {expanded === rec.id && (
                <div className="px-4 pb-4 pt-1 border-t border-slate-700 space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
                    {rec.author && (
                      <div>
                        <span className="text-slate-500">Author: </span>
                        <span className="text-slate-200">{rec.author}</span>
                      </div>
                    )}
                    <div>
                      <span className="text-slate-500">Source: </span>
                      <a
                        href={rec.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1"
                      >
                        {rec.source_url.length > 60 ? rec.source_url.slice(0, 60) + '...' : rec.source_url}
                        <ExternalLink size={12} />
                      </a>
                    </div>
                    <div>
                      <span className="text-slate-500">Email: </span>
                      <a href={`mailto:${rec.requester_email}`} className="text-indigo-400 hover:text-indigo-300">{rec.requester_email}</a>
                    </div>
                    <div>
                      <span className="text-slate-500">Submitted: </span>
                      <span className="text-slate-200">{rec.created_at}</span>
                    </div>
                  </div>

                  {rec.description && (
                    <div className="text-sm">
                      <span className="text-slate-500">Description: </span>
                      <span className="text-slate-300">{rec.description}</span>
                    </div>
                  )}

                  {rec.notes && (
                    <div className="text-sm">
                      <span className="text-slate-500">User notes: </span>
                      <span className="text-slate-300">{rec.notes}</span>
                    </div>
                  )}

                  {/* Admin notes */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">Admin Notes</label>
                    <textarea
                      value={editingNotes[rec.id] ?? rec.admin_notes ?? ''}
                      onChange={e => setEditingNotes(prev => ({ ...prev, [rec.id]: e.target.value }))}
                      rows={2}
                      className="input w-full text-sm resize-none"
                      placeholder="Internal notes..."
                    />
                    <button
                      onClick={() => handleSaveNotes(rec.id)}
                      className="btn-secondary text-xs mt-1"
                    >
                      Save Notes
                    </button>
                  </div>

                  {/* Email the requester */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1 flex items-center gap-1">
                      <Mail size={12} /> Email requester ({rec.requester_email})
                    </label>
                    <textarea
                      value={emailDraft[rec.id] ?? ''}
                      onChange={e => setEmailDraft(prev => ({ ...prev, [rec.id]: e.target.value }))}
                      rows={3}
                      className="input w-full text-sm resize-none"
                      placeholder="e.g. Could you confirm which translation of the source you're reading, and a link to the raws?"
                    />
                    <div className="flex items-center gap-2 mt-1">
                      <button
                        onClick={() => handleSendEmail(rec.id)}
                        disabled={emailState[rec.id]?.sending || !(emailDraft[rec.id] ?? '').trim()}
                        className="btn-secondary text-xs flex items-center gap-1 disabled:opacity-40"
                      >
                        {emailState[rec.id]?.sending
                          ? <><Loader2 size={12} className="animate-spin" /> Sending…</>
                          : <><Mail size={12} /> Send Email</>}
                      </button>
                      {emailState[rec.id]?.result && (
                        <span className={`text-xs ${emailState[rec.id].result.ok ? 'text-emerald-400' : 'text-amber-400'}`}>
                          {emailState[rec.id].result.text}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-600 mt-1">
                      Sends from the site address; replies come back to you. Includes an unsubscribe link.
                    </p>
                  </div>

                  {/* Replies (ingested from the editor mailbox) */}
                  <RepliesThread recId={rec.id} onRead={invalidate} />

                  {/* Actions */}
                  <div className="flex items-center gap-2 pt-1 flex-wrap">
                    <span className="text-xs text-slate-500 mr-1">Set status:</span>
                    {STATUS_OPTIONS.map(s => (
                      <button
                        key={s}
                        onClick={() => handleStatusChange(rec.id, s)}
                        disabled={rec.status === s}
                        className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                          rec.status === s
                            ? 'bg-indigo-600 text-white cursor-default'
                            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                      >
                        {s}
                      </button>
                    ))}
                    <div className="flex-1" />
                    <button
                      onClick={() => handleDelete(rec.id)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded text-xs text-rose-400 hover:bg-rose-500/20 transition-colors"
                    >
                      <Trash2 size={12} /> Delete
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// One reply, rendered as a small quoted card.
function ReplyCard({ reply, showFrom }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/40 px-3 py-2">
      <div className="flex items-center gap-2 text-xs text-slate-400 mb-1 flex-wrap">
        <CornerDownRight size={12} className="text-slate-500" />
        <span className="font-medium text-slate-300">
          {reply.from_name || reply.from_email || 'Unknown sender'}
        </span>
        {reply.from_name && reply.from_email && (
          <span className="text-slate-500">&lt;{reply.from_email}&gt;</span>
        )}
        {reply.received_at && <span className="text-slate-500">· {reply.received_at}</span>}
        {reply.correlation && reply.correlation !== 'plus' && (
          <span className="px-1.5 py-0.5 rounded bg-slate-700 text-slate-400 text-[10px]">
            {reply.correlation === 'msgid' ? 'matched by thread'
              : reply.correlation === 'unmatched' ? 'unmatched' : reply.correlation}
          </span>
        )}
      </div>
      {reply.subject && <div className="text-xs text-slate-500 mb-1">{reply.subject}</div>}
      <div className="text-sm text-slate-200 whitespace-pre-wrap break-words">{reply.body}</div>
      {showFrom && (
        <a href={`mailto:${reply.from_email}`} className="text-xs text-indigo-400 hover:text-indigo-300 mt-1 inline-block">
          Reply by email →
        </a>
      )}
    </div>
  )
}

// Fetches + shows the reply thread for a request; marks unread replies read on open.
function RepliesThread({ recId, onRead }) {
  const { data, isPending } = useQuery({
    queryKey: ['rec-replies', recId],
    queryFn: () => api.listReplies(recId),
    refetchInterval: 60000,
  })
  const replies = data?.items || []

  useEffect(() => {
    if (replies.some(r => !r.is_read)) {
      api.markRepliesRead(recId)
        .then(() => onRead && onRead())
        .catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recId, replies.length])

  if (isPending) {
    return <div className="text-xs text-slate-500 flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Loading replies…</div>
  }
  if (replies.length === 0) {
    return <div className="text-xs text-slate-600">No email replies yet.</div>
  }
  return (
    <div>
      <div className="text-xs text-slate-500 mb-1 flex items-center gap-1">
        <CornerDownRight size={12} /> Replies ({replies.length})
      </div>
      <div className="space-y-2">
        {replies.map(r => <ReplyCard key={r.id} reply={r} />)}
      </div>
    </div>
  )
}
