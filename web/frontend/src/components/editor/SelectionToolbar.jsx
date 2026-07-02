import { BookOpen, Languages } from 'lucide-react'

// ── Floating toolbar that appears on Chinese text selection ───────────
export default function SelectionToolbar({ position, onLookup, onRetranslate }) {
  if (!position) return null
  return (
    <div
      className="fixed z-40 bg-slate-800 border border-slate-700 rounded-md shadow-lg
                 flex items-center gap-0.5 p-0.5"
      style={{
        left: Math.min(position.x, window.innerWidth - 200),
        top: position.y - 36,
      }}
    >
      <button
        className="px-2.5 py-1 text-xs text-slate-300 hover:text-white hover:bg-slate-700
                   rounded flex items-center gap-1.5"
        onMouseDown={(e) => e.preventDefault()}
        onClick={onLookup}
      >
        <BookOpen size={12} /> Dictionary
      </button>
      <button
        className="px-2.5 py-1 text-xs text-slate-300 hover:text-white hover:bg-slate-700
                   rounded flex items-center gap-1.5"
        onMouseDown={(e) => e.preventDefault()}
        onClick={onRetranslate}
      >
        <Languages size={12} /> Retranslate
      </button>
    </div>
  )
}
