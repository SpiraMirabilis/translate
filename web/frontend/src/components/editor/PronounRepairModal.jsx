import { useState, useEffect } from 'react'
import { api } from '../../services/api'
import { Loader2, Sparkles, X } from 'lucide-react'

export default function PronounRepairModal({ bookId, chapterNumber, onResult, onClose }) {
  const [loading, setLoading] = useState(true)
  const [entities, setEntities] = useState([])
  const [entityId, setEntityId] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    api.listChapterGenderedEntities(bookId, chapterNumber)
      .then(d => { if (!cancelled) { setEntities(d.entities || []); setLoading(false) } })
      .catch(e => { if (!cancelled) { setError(e.message); setLoading(false) } })
    return () => { cancelled = true }
  }, [bookId, chapterNumber])

  const handleRun = async () => {
    if (!entityId) return
    setRunning(true); setError(null)
    try {
      const res = await api.pronounRepairChapter(bookId, chapterNumber, parseInt(entityId, 10))
      onResult(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-emerald-400" />
            <h2 className="font-semibold text-slate-200">Repair pronouns in this chapter</h2>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
            <X size={16} />
          </button>
        </div>

        <p className="text-sm text-slate-400">
          Pick a character whose pronouns are wrong in this chapter. A small model will scan
          paragraphs mentioning them and rewrite the pronouns to match the character&rsquo;s
          recorded gender. The entity record itself is not changed.
        </p>

        {loading ? (
          <div className="flex items-center gap-2 text-slate-400 text-sm py-2">
            <Loader2 size={14} className="animate-spin" /> Loading characters…
          </div>
        ) : entities.length === 0 ? (
          <p className="text-sm text-amber-400">
            No character entities with a defined gender appear in this chapter&rsquo;s translated text.
            Set the gender on the relevant entity first, or check that the character&rsquo;s English name
            is actually present in the translation.
          </p>
        ) : (
          <div>
            <label className="label">Character ({entities.length} in this chapter)</label>
            <select
              className="input text-sm"
              value={entityId}
              onChange={e => setEntityId(e.target.value)}
              disabled={running}
            >
              <option value="">— Pick a character —</option>
              {entities.map(e => (
                <option key={e.entity_id} value={e.entity_id}>
                  {e.translation} ({e.gender}){e.untranslated ? ` — ${e.untranslated}` : ''}
                </option>
              ))}
            </select>
          </div>
        )}

        {error && <p className="text-rose-400 text-sm">{error}</p>}

        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose} disabled={running}>Cancel</button>
          <button
            className="btn-primary flex items-center gap-1.5"
            onClick={handleRun}
            disabled={running || !entityId || entities.length === 0}
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            {running ? 'Repairing…' : 'Run repair'}
          </button>
        </div>
      </div>
    </div>
  )
}
