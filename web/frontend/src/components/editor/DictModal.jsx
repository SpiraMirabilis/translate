import { useEffect, useRef } from 'react'
import { BookOpen, Loader2, X } from 'lucide-react'
import { pinyinToMarked } from '../../lib/editorHighlights'

// ── Dictionary Lookup Modal ──────────────────────────────────────────
export default function DictModal({ query, data, loading, error, position, onClose }) {
  const ref = useRef(null)

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const style = {}
  if (position) {
    style.position = 'fixed'
    style.left = Math.min(position.x, window.innerWidth - 420)
    style.top = Math.min(position.y + 8, window.innerHeight - 400)
    style.zIndex = 50
  }

  return (
    <div ref={ref} style={style}
      className="w-[400px] max-w-[90vw] max-h-[380px] overflow-y-auto bg-slate-900 border border-slate-700 rounded-lg shadow-2xl"
    >
      <div className="sticky top-0 bg-slate-900 border-b border-slate-800 px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpen size={14} className="text-indigo-400" />
          <span className="text-base font-medium text-slate-100">{query}</span>
          {data?.exact?.[0]?.pinyin && (
            <span className="text-sm text-amber-400/90">{pinyinToMarked(data.exact[0].pinyin)}</span>
          )}
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
          <X size={14} />
        </button>
      </div>

      <div className="px-4 py-3 space-y-3 text-sm">
        {loading && (
          <div className="flex items-center gap-2 text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Looking up...
          </div>
        )}
        {error && <p className="text-rose-400 text-xs">{error}</p>}
        {data?.exact?.length > 0 && (
          <div>
            {data.exact.map((entry, i) => (
              <DictEntry key={i} entry={entry} highlight />
            ))}
          </div>
        )}
        {data && data.exact?.length === 0 && !loading && (
          <p className="text-slate-500 text-xs italic">No exact match found.</p>
        )}
        {data?.characters?.length > 0 && data.characters[0]?.pinyin && (
          <div>
            <div className="text-xs text-slate-600 uppercase tracking-wider mb-1.5">
              Character breakdown
            </div>
            {data.characters.map((entry, i) => (
              <DictEntry key={i} entry={entry} />
            ))}
          </div>
        )}
        {data?.compounds?.length > 0 && (
          <div>
            <div className="text-xs text-slate-600 uppercase tracking-wider mb-1.5">
              Compound words ({data.compounds.length})
            </div>
            {data.compounds.map((entry, i) => (
              <DictEntry key={i} entry={entry} compact />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function DictEntry({ entry, highlight, compact }) {
  return (
    <div className={`${compact ? 'py-1' : 'py-1.5'} ${highlight ? '' : 'opacity-80'}`}>
      <div className="flex items-baseline gap-2 flex-wrap">
        {!compact && entry.traditional !== entry.simplified && (
          <span className="text-slate-500 text-xs">{entry.traditional}</span>
        )}
        <span className={`${highlight ? 'text-indigo-300' : 'text-slate-300'} ${compact ? 'text-xs' : 'text-sm'} font-medium`}>
          {entry.simplified}
        </span>
        <span className="text-amber-400/80 text-xs">{pinyinToMarked(entry.pinyin)}</span>
        <span className="text-slate-600 text-xs">{entry.pinyin}</span>
      </div>
      <div className="text-slate-400 text-xs mt-0.5 leading-relaxed">
        {entry.definitions.filter(Boolean).join('; ')}
      </div>
    </div>
  )
}
