import { useState, useEffect, useCallback } from 'react'
import { MessageSquare, Trash2, Check, X, Ban, Sparkles, Loader2, ChevronDown, ChevronUp, Shield } from 'lucide-react'
import { api } from '../services/api'
import { useSite } from '../App'

const STATUS_TABS = [
  { value: 'pending',  label: 'Pending',  color: 'bg-amber-500/20 text-amber-300' },
  { value: 'approved', label: 'Approved', color: 'bg-emerald-500/20 text-emerald-300' },
  { value: 'blocked',  label: 'Blocked',  color: 'bg-rose-500/20 text-rose-300' },
  { value: 'deleted',  label: 'Deleted',  color: 'bg-slate-500/20 text-slate-400' },
]

function relTime(iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (!t) return iso
  const diff = (Date.now() - t) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  return new Date(iso).toLocaleString()
}

export default function CommentsAdmin() {
  const { site_name } = useSite()
  const [filter, setFilter] = useState('pending')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(null)
  const [busy, setBusy] = useState({})
  const [bans, setBans] = useState([])
  const [showBans, setShowBans] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.listComments({ status: filter, limit: 200 })
      .then(data => setItems(data.items || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [filter])

  const loadBans = useCallback(() => {
    api.listCommentBans()
      .then(data => setBans(data.items || []))
      .catch(() => setBans([]))
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadBans() }, [loadBans])

  useEffect(() => {
    document.title = `Comments | ${site_name}`
    return () => { document.title = site_name }
  }, [site_name])

  const setItemBusy = (id, val) => setBusy(prev => ({ ...prev, [id]: val }))

  const handleStatus = async (id, newStatus) => {
    setItemBusy(id, true)
    try {
      await api.updateCommentAdmin(id, { status: newStatus })
      setItems(prev => prev.filter(c => c.id !== id))
    } finally { setItemBusy(id, false) }
  }

  const handleSoftDelete = async (id) => {
    if (!confirm('Soft-delete this comment? Body becomes [removed], thread structure is preserved.')) return
    setItemBusy(id, true)
    try {
      await api.deleteCommentAdmin(id, true)
      setItems(prev => prev.filter(c => c.id !== id))
    } finally { setItemBusy(id, false) }
  }

  const handleHardDelete = async (id) => {
    if (!confirm('Hard-delete this comment? Will fail if it has replies.')) return
    setItemBusy(id, true)
    try {
      await api.deleteCommentAdmin(id, false)
      setItems(prev => prev.filter(c => c.id !== id))
    } catch (e) {
      alert(e.message || 'Hard delete failed.')
    } finally { setItemBusy(id, false) }
  }

  const handleBan = async (kind, value, reason) => {
    if (!value) return
    if (!confirm(`Ban ${kind} = ${value}? ${kind === 'ip' ? 'IP will also be pushed to Cloudflare edge.' : ''}`)) return
    try {
      await api.createCommentBan({ kind, value, reason: reason || `via comments-admin` })
      loadBans()
      alert(`Banned ${kind}: ${value}`)
    } catch (e) {
      alert(e.message || 'Ban failed.')
    }
  }

  const handleRerunAutomod = async (id) => {
    setItemBusy(id, true)
    try {
      const result = await api.rerunAutomod(id)
      alert(`Verdict: ${result.verdict}\nReason: ${result.reason}`)
      load()
    } catch (e) {
      alert(e.message || 'Automod failed.')
    } finally { setItemBusy(id, false) }
  }

  const handleUnban = async (id) => {
    if (!confirm('Remove this ban?')) return
    try {
      await api.removeCommentBan(id)
      loadBans()
    } catch (e) {
      alert(e.message || 'Unban failed.')
    }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <MessageSquare size={24} className="text-indigo-400" />
        <h1 className="text-2xl font-bold text-slate-100">Comments</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 flex-wrap">
        {STATUS_TABS.map(tab => (
          <button
            key={tab.value}
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
        <button
          onClick={() => setShowBans(v => !v)}
          className={`ml-auto px-3 py-1.5 rounded-lg text-sm font-medium inline-flex items-center gap-1.5 transition-colors ${
            showBans
              ? 'bg-rose-600 text-white'
              : 'bg-slate-800 text-slate-400 hover:text-slate-200 hover:bg-slate-700'
          }`}
        >
          <Shield size={14} /> Bans ({bans.length})
        </button>
      </div>

      {/* Bans panel */}
      {showBans && (
        <div className="mb-4 p-3 bg-slate-900 border border-slate-700 rounded-lg">
          <h3 className="text-sm font-semibold text-slate-200 mb-2">Active bans</h3>
          {bans.length === 0 ? (
            <p className="text-sm text-slate-500">No active bans.</p>
          ) : (
            <div className="space-y-1">
              {bans.map(b => (
                <div key={b.id} className="flex items-center gap-2 text-sm">
                  <span className="font-mono text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300">{b.kind}</span>
                  <span className="font-mono text-slate-300 truncate">{b.value}</span>
                  {b.kind === 'ip' && b.cf_pushed === 1 && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300" title="Pushed to Cloudflare edge">CF</span>
                  )}
                  {b.reason && <span className="text-xs text-slate-500 truncate flex-1">— {b.reason}</span>}
                  <button onClick={() => handleUnban(b.id)} className="ml-auto text-rose-400 hover:text-rose-300 text-xs">
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Comments list */}
      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 size={28} className="animate-spin text-indigo-400" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <MessageSquare size={40} className="mx-auto mb-3 opacity-30" />
          <p>No {filter} comments.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(c => {
            const isExp = expanded === c.id
            return (
              <div key={c.id} className="card border border-slate-700 rounded-lg overflow-hidden">
                <div className="p-3">
                  <div className="flex items-center gap-2 text-sm mb-1.5 flex-wrap">
                    <span className="font-medium text-slate-200">{c.display_name}</span>
                    <span className="text-xs text-slate-500 font-mono">#{(c.commenter_uuid || '').slice(-8)}</span>
                    <span className="text-xs text-slate-500">·</span>
                    <span className="text-xs text-slate-500">book {c.book_id} ch {c.chapter_number}</span>
                    {c.parent_id && <span className="text-xs text-slate-500">· reply to #{c.parent_id}</span>}
                    <span className="text-xs text-slate-500">· {relTime(c.created_at)}</span>
                    {c.edited_at && <span className="text-xs text-slate-500 italic">· edited</span>}
                    {c.automod_state && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        c.automod_state === 'genuine' ? 'bg-emerald-500/20 text-emerald-300'
                        : c.automod_state === 'spam' ? 'bg-rose-500/20 text-rose-300'
                        : c.automod_state === 'unsure' ? 'bg-amber-500/20 text-amber-300'
                        : 'bg-slate-700 text-slate-400'
                      }`} title={c.automod_reason || ''}>
                        AI: {c.automod_state}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-slate-300 whitespace-pre-wrap break-words">{c.body}</p>

                  {/* Action row */}
                  <div className="mt-2 flex flex-wrap gap-1.5 items-center">
                    {c.status !== 'approved' && (
                      <button
                        onClick={() => handleStatus(c.id, 'approved')}
                        disabled={busy[c.id]}
                        className="px-2 py-1 rounded text-xs bg-emerald-600 hover:bg-emerald-700 text-white inline-flex items-center gap-1 disabled:opacity-50"
                      >
                        <Check size={12} /> Approve
                      </button>
                    )}
                    {c.status !== 'pending' && (
                      <button
                        onClick={() => handleStatus(c.id, 'pending')}
                        disabled={busy[c.id]}
                        className="px-2 py-1 rounded text-xs bg-amber-600 hover:bg-amber-700 text-white inline-flex items-center gap-1 disabled:opacity-50"
                      >
                        Re-pend
                      </button>
                    )}
                    {c.status !== 'blocked' && (
                      <button
                        onClick={() => handleStatus(c.id, 'blocked')}
                        disabled={busy[c.id]}
                        className="px-2 py-1 rounded text-xs bg-rose-600 hover:bg-rose-700 text-white inline-flex items-center gap-1 disabled:opacity-50"
                      >
                        <X size={12} /> Block
                      </button>
                    )}
                    <button
                      onClick={() => handleRerunAutomod(c.id)}
                      disabled={busy[c.id]}
                      className="px-2 py-1 rounded text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 inline-flex items-center gap-1 disabled:opacity-50"
                      title="Re-run AI moderation"
                    >
                      <Sparkles size={12} /> AI
                    </button>
                    <button
                      onClick={() => setExpanded(isExp ? null : c.id)}
                      className="px-2 py-1 rounded text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 inline-flex items-center gap-1"
                    >
                      {isExp ? <ChevronUp size={12} /> : <ChevronDown size={12} />} Details
                    </button>
                  </div>

                  {/* Expanded details */}
                  {isExp && (
                    <div className="mt-3 pt-3 border-t border-slate-700 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-slate-400">
                      <div>
                        <span className="font-medium text-slate-300">UUID:</span>{' '}
                        <span className="font-mono break-all">{c.commenter_uuid}</span>
                        <button
                          onClick={() => handleBan('uuid', c.commenter_uuid, `comment #${c.id}`)}
                          className="ml-2 text-rose-400 hover:text-rose-300 inline-flex items-center gap-1"
                        >
                          <Ban size={10} /> ban UUID
                        </button>
                      </div>
                      <div>
                        <span className="font-medium text-slate-300">Email:</span>{' '}
                        <span className="font-mono break-all">{c.email || '(none)'}</span>
                        {c.email && (
                          <button
                            onClick={() => handleBan('email', c.email, `comment #${c.id}`)}
                            className="ml-2 text-rose-400 hover:text-rose-300 inline-flex items-center gap-1"
                          >
                            <Ban size={10} /> ban email
                          </button>
                        )}
                      </div>
                      <div>
                        <span className="font-medium text-slate-300">IP:</span>{' '}
                        <span className="font-mono">{c.ip}</span>
                        <button
                          onClick={() => handleBan('ip', c.ip, `comment #${c.id}`)}
                          className="ml-2 text-rose-400 hover:text-rose-300 inline-flex items-center gap-1"
                        >
                          <Ban size={10} /> ban IP (CF)
                        </button>
                      </div>
                      <div>
                        <span className="font-medium text-slate-300">User-Agent:</span>{' '}
                        <span className="break-all opacity-80">{c.user_agent || '(none)'}</span>
                      </div>
                      {c.automod_reason && (
                        <div className="sm:col-span-2">
                          <span className="font-medium text-slate-300">AI reason:</span>{' '}
                          {c.automod_reason}
                        </div>
                      )}
                      <div className="sm:col-span-2 flex gap-2 pt-2">
                        <button
                          onClick={() => handleSoftDelete(c.id)}
                          className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-200 inline-flex items-center gap-1"
                        >
                          <Trash2 size={12} /> Soft-delete
                        </button>
                        <button
                          onClick={() => handleHardDelete(c.id)}
                          className="px-2 py-1 rounded bg-rose-700 hover:bg-rose-600 text-white inline-flex items-center gap-1"
                        >
                          <Trash2 size={12} /> Hard-delete
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
