import { useState, useEffect } from 'react'
import { api } from '../../services/api'
import { useWs } from '../../App'
import { X, Loader2, Globe } from 'lucide-react'

export default function WordPressPublishModal({ book, onClose }) {
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState(null)
  const [storyStatus, setStoryStatus] = useState('Ongoing')
  const [storyRating, setStoryRating] = useState('Everyone')
  const [chapterGroup, setChapterGroup] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [progress, setProgress] = useState(null) // { current, total, title }
  const [result, setResult] = useState(null) // { created, updated, skipped, errors }
  const [error, setError] = useState(null)
  const { subscribe } = useWs()

  useEffect(() => {
    api.wpBookStatus(book.id)
      .then(d => setStatus(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [book.id])

  // Listen to WebSocket for publish progress — use subscribe() to guarantee
  // every message is processed, even when messages arrive faster than React renders.
  useEffect(() => {
    return subscribe((m) => {
      if (m.type !== 'wp_publish') return
      if (m.step === 'chapter') {
        setProgress({ current: m.current, total: m.total, title: m.title })
      } else if (m.step === 'done') {
        setResult({ created: m.created, updated: m.updated, skipped: m.skipped, errors: m.errors })
        setPublishing(false)
      } else if (m.step === 'error') {
        setError(m.error)
        setPublishing(false)
      } else if (m.step === 'cancelled') {
        setPublishing(false)
        setError('Publish cancelled.')
      }
    })
  }, [subscribe])

  const handlePublish = async () => {
    setPublishing(true)
    setProgress(null)
    setResult(null)
    setError(null)
    try {
      await api.wpPublish(book.id, {
        story_status: storyStatus,
        story_rating: storyRating,
        chapter_group: chapterGroup,
      })
    } catch (e) {
      setError(e.message)
      setPublishing(false)
    }
  }

  const handleCancel = async () => {
    try { await api.wpCancelPublish(book.id) } catch { /* ignore */ }
  }

  const statusBadge = (s) => {
    if (s === 'published') return <span className="badge-emerald text-xs">Published</span>
    if (s === 'changed') return <span className="badge-amber text-xs">Changed</span>
    return <span className="badge-slate text-xs">New</span>
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-200">Publish to WordPress</h2>
            <p className="text-xs text-slate-500 mt-0.5">{book.title}</p>
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center p-12 text-slate-400 text-sm">
            <Loader2 size={14} className="animate-spin mr-2" /> Loading status...
          </div>
        ) : (
          <div className="flex-1 overflow-auto p-5 space-y-4">
            {/* Story info */}
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Globe size={14} />
              {status?.story_published
                ? <span>Story published (WP ID: {status.story_wp_post_id})</span>
                : <span>Story not yet published</span>
              }
            </div>

            {/* Options */}
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="label">Story Status</label>
                <select className="input text-sm" value={storyStatus} onChange={e => setStoryStatus(e.target.value)}>
                  <option>Ongoing</option>
                  <option>Completed</option>
                  <option>Hiatus</option>
                  <option>Canceled</option>
                </select>
              </div>
              <div>
                <label className="label">Rating</label>
                <select className="input text-sm" value={storyRating} onChange={e => setStoryRating(e.target.value)}>
                  <option>Everyone</option>
                  <option>Teen</option>
                  <option>Mature</option>
                  <option>Adult</option>
                </select>
              </div>
              <div>
                <label className="label">Chapter Group</label>
                <input
                  className="input text-sm"
                  value={chapterGroup}
                  onChange={e => setChapterGroup(e.target.value)}
                  placeholder="e.g. Volume 1"
                />
              </div>
            </div>

            {/* Progress — above chapter list so it's always visible */}
            {publishing && progress && (
              <div className="space-y-2">
                <div className="flex justify-between text-xs text-slate-400">
                  <span>Publishing: {progress.title}</span>
                  <span>{progress.current} / {progress.total}</span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-2">
                  <div
                    className="bg-blue-500 h-2 rounded-full transition-all"
                    style={{ width: `${(progress.current / progress.total) * 100}%` }}
                  />
                </div>
              </div>
            )}

            {/* Result */}
            {result && (
              <div className="bg-emerald-950/50 border border-emerald-800 rounded px-3 py-2 text-sm text-emerald-300">
                Done: {result.created} created, {result.updated} updated, {result.skipped} skipped
                {result.errors > 0 && <span className="text-rose-400">, {result.errors} errors</span>}
              </div>
            )}

            {error && <p className="text-rose-400 text-sm">{error}</p>}

            {/* Chapters table */}
            {status?.chapters?.length > 0 && (
              <div className="border border-slate-700 rounded overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-slate-500 bg-slate-800/50">
                      <th className="text-left px-3 py-2 font-medium">Ch.</th>
                      <th className="text-left px-3 py-2 font-medium">Title</th>
                      <th className="text-left px-3 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.chapters.map(ch => (
                      <tr key={ch.chapter_number} className="border-t border-slate-800">
                        <td className="px-3 py-1.5 text-slate-400 font-mono">{ch.chapter_number}</td>
                        <td className="px-3 py-1.5 text-slate-300 truncate max-w-[300px]">{ch.title}</td>
                        <td className="px-3 py-1.5">{statusBadge(ch.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-700 shrink-0">
          <button className="btn-secondary" onClick={onClose}>Close</button>
          {publishing ? (
            <button className="btn-danger flex items-center gap-1.5" onClick={handleCancel}>
              <X size={13} /> Cancel
            </button>
          ) : (
            <button
              className="btn-primary flex items-center gap-1.5"
              onClick={handlePublish}
              disabled={loading || !status?.chapters?.length}
            >
              <Globe size={13} /> Publish All
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
