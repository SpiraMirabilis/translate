import { useState, useEffect } from 'react'
import { api } from '../../services/api'
import { X, Loader2, Sparkles } from 'lucide-react'
import ComboBox from '../ComboBox'

export default function PronounRepairBookModal({ book, onClose }) {
  const [chapters, setChapters] = useState([])
  const [chaptersLoading, setChaptersLoading] = useState(true)
  const [chapterQuery, setChapterQuery] = useState('')
  const [chapterNum, setChapterNum] = useState(null)
  const [chapterEntities, setChapterEntities] = useState([])
  const [entitiesLoading, setEntitiesLoading] = useState(false)
  const [characterQuery, setCharacterQuery] = useState('')
  const [entityId, setEntityId] = useState(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setChaptersLoading(true); setError(null)
    api.listChapters(book.id)
      .then(d => { if (!cancelled) { setChapters(d.chapters || []); setChaptersLoading(false) } })
      .catch(e => { if (!cancelled) { setError(e.message); setChaptersLoading(false) } })
    return () => { cancelled = true }
  }, [book.id])

  const chapterOptionMap = new Map()
  const chapterOptions = chapters.map(ch => {
    const label = `${ch.chapter}: ${ch.title || '(untitled)'}`
    chapterOptionMap.set(label, ch.chapter)
    return label
  })

  const handleChapterChange = (str) => {
    setChapterQuery(str)
    let num = chapterOptionMap.get(str)
    if (num === undefined) {
      const m = String(str).trim().match(/^(\d+)\b/)
      const parsed = m ? parseInt(m[1], 10) : NaN
      num = chapters.some(c => c.chapter === parsed) ? parsed : null
    }
    const resolved = num || null
    // ComboBox calls onChange on every outside-click commit (including clicking
    // into the character combobox). Bail out if the chapter didn't actually change
    // so we don't wipe the character list mid-pick.
    if (resolved === chapterNum) return

    setResult(null)
    setError(null)
    setChapterNum(resolved)
    setCharacterQuery('')
    setEntityId(null)
    setChapterEntities([])
    if (resolved) {
      setEntitiesLoading(true)
      api.listChapterGenderedEntities(book.id, resolved)
        .then(d => setChapterEntities(d.entities || []))
        .catch(e => setError(e.message))
        .finally(() => setEntitiesLoading(false))
    }
  }

  const entityOptionMap = new Map()
  const entityOptions = chapterEntities.map(e => {
    const label = `${e.translation} (${e.gender})${e.untranslated ? ` — ${e.untranslated}` : ''}`
    entityOptionMap.set(label, e.entity_id)
    return label
  })

  const handleCharacterChange = (str) => {
    setCharacterQuery(str)
    const id = entityOptionMap.get(str) || null
    if (id === entityId) return
    setResult(null)
    setEntityId(id)
  }

  const handleRun = async () => {
    if (!chapterNum || !entityId) return
    setRunning(true); setError(null); setResult(null)
    try {
      const res = await api.pronounRepairChapter(book.id, chapterNum, entityId)
      setResult(res)
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
            <h2 className="font-semibold text-slate-200">Repair Chapter Pronouns</h2>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
            <X size={16} />
          </button>
        </div>

        <p className="text-sm text-slate-400">
          Run a surgical pronoun fix on a single chapter of <span className="text-slate-300">{book.title}</span>.
          A small model scans paragraphs mentioning the chosen character and rewrites pronouns to match their recorded gender.
        </p>

        <div>
          <label className="label">Chapter</label>
          {chaptersLoading ? (
            <div className="flex items-center gap-2 text-slate-400 text-sm py-2">
              <Loader2 size={14} className="animate-spin" /> Loading chapters…
            </div>
          ) : (
            <ComboBox
              value={chapterQuery}
              onChange={handleChapterChange}
              options={chapterOptions}
              placeholder="Type a chapter number or title…"
            />
          )}
        </div>

        <div>
          <label className="label">Character</label>
          {!chapterNum ? (
            <p className="text-xs text-slate-500">Pick a chapter first.</p>
          ) : entitiesLoading ? (
            <div className="flex items-center gap-2 text-slate-400 text-sm py-2">
              <Loader2 size={14} className="animate-spin" /> Loading characters in chapter {chapterNum}…
            </div>
          ) : chapterEntities.length === 0 ? (
            <p className="text-xs text-amber-400">
              No character entities with a defined gender appear in this chapter&rsquo;s translated text.
            </p>
          ) : (
            <ComboBox
              value={characterQuery}
              onChange={handleCharacterChange}
              options={entityOptions}
              placeholder={`Type a name… (${chapterEntities.length} in chapter ${chapterNum})`}
            />
          )}
        </div>

        {error && <p className="text-rose-400 text-sm">{error}</p>}

        {result && (
          <div className="card p-3 bg-emerald-950/40 border-emerald-700/50 text-sm text-emerald-200 flex items-start gap-2">
            <Sparkles size={14} className="text-emerald-400 mt-0.5 shrink-0" />
            <span>
              {result.paragraphs_changed > 0
                ? `Repaired ${result.paragraphs_changed} paragraph${result.paragraphs_changed === 1 ? '' : 's'} for ${result.character_name} in chapter ${chapterNum} (${result.windows_examined} windows examined).`
                : `No changes needed for ${result.character_name} in chapter ${chapterNum} (${result.windows_examined} windows examined).`}
              {result.errors ? ` ${result.errors} window error(s).` : ''}
            </span>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose} disabled={running}>
            {result ? 'Close' : 'Cancel'}
          </button>
          <button
            className="btn-primary flex items-center gap-1.5"
            onClick={handleRun}
            disabled={running || !chapterNum || !entityId}
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            {running ? 'Repairing…' : 'Run repair'}
          </button>
        </div>
      </div>
    </div>
  )
}
