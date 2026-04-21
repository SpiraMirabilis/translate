import { useState, useEffect, useCallback } from 'react'
import { MessageSquarePlus, Trash2, ExternalLink, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'
import { api } from '../services/api'

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
]

export default function Recommendations() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [editingNotes, setEditingNotes] = useState({})

  const load = useCallback(() => {
    setLoading(true)
    api.listRecommendations(filter)
      .then(data => setItems(data.items || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [filter])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    document.title = 'Recommendations | T9'
    return () => { document.title = 'T9' }
  }, [])

  const handleStatusChange = async (id, newStatus) => {
    await api.updateRecommendation(id, { status: newStatus })
    setItems(prev => prev.map(r =>
      r.id === id ? { ...r, status: newStatus } : r
    ))
  }

  const handleSaveNotes = async (id) => {
    const notes = editingNotes[id] ?? ''
    await api.updateRecommendation(id, { admin_notes: notes })
    setItems(prev => prev.map(r =>
      r.id === id ? { ...r, admin_notes: notes } : r
    ))
  }

  const handleDelete = async (id) => {
    if (!confirm('Delete this recommendation?')) return
    await api.deleteRecommendation(id)
    setItems(prev => prev.filter(r => r.id !== id))
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
