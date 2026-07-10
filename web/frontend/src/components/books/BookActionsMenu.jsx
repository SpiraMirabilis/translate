import { useState, useEffect, useRef } from 'react'
import {
  Trash2, Edit2, Download, ChevronDown, Loader2, ScrollText, Sparkles, Globe, Tags, ListChecks, Code, Boxes, RefreshCw
} from 'lucide-react'

export default function BookActionsMenu({ book, exporting, onExport, onPublish, onCategories, onReview, onPrompt, onEdit, onDelete, onApiLogs, onPronounRepair, onModules, onInvalidateCache }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handleClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  const item = (icon, label, onClick, className = '') => (
    <button
      className={`text-xs text-left px-3 py-1.5 hover:bg-slate-700 text-slate-300 flex items-center gap-2 w-full ${className}`}
      onClick={() => { setOpen(false); onClick() }}
    >
      {icon} {label}
    </button>
  )

  const isExporting = exporting && exporting.startsWith(`${book.id}-`)
  const exportFormat = isExporting ? exporting.split('-').pop().toUpperCase() : null

  // Original works never run the translation pipeline, so everything it feeds
  // (entities, per-book prompt, source modules, API call log) is empty for them.
  const isTranslation = !book.is_original

  return (
    <div className="relative" ref={ref}>
      <button className="btn-ghost p-1.5 flex items-center gap-0.5 text-xs" onClick={() => setOpen(v => !v)}>
        {isExporting ? (
          <>
            <Loader2 size={12} className="animate-spin" />
            <span className="text-indigo-400">Preparing {exportFormat}...</span>
          </>
        ) : (
          <>Actions <ChevronDown size={12} /></>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 min-w-[180px] bg-slate-800 border border-slate-700 rounded shadow-xl flex flex-col py-1">
          {/* Export submenu */}
          <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Export</div>
          {['text', 'markdown', 'html', 'epub', 'azw3'].map(fmt => (
            <button
              key={fmt}
              className="text-xs text-left px-3 py-1.5 hover:bg-slate-700 text-slate-300 flex items-center gap-2 disabled:opacity-50"
              onClick={() => { setOpen(false); onExport(book.id, fmt) }}
              disabled={!!exporting}
            >
              {exporting === `${book.id}-${fmt}` ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
              {fmt.toUpperCase()}
            </button>
          ))}
          {item(<RefreshCw size={12} />, 'Clear EPUB Cache', onInvalidateCache)}
          <div className="border-t border-slate-700 my-1" />
          <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Publish</div>
          {item(<Globe size={12} />, 'WordPress', onPublish)}
          {isTranslation && (
            <>
              <div className="border-t border-slate-700 my-1" />
              <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Entities</div>
              {item(<Tags size={12} />, 'Categories', onCategories)}
              {item(<ListChecks size={12} />, 'Review Entities', onReview)}
              <div className="border-t border-slate-700 my-1" />
              <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Chapters</div>
              {item(<Sparkles size={12} />, 'Repair Chapter Pronouns', onPronounRepair)}
            </>
          )}
          <div className="border-t border-slate-700 my-1" />
          <div className="px-3 py-1 text-[10px] text-slate-500 uppercase tracking-wider">Settings</div>
          {isTranslation && item(<ScrollText size={12} />, 'System Prompt', onPrompt)}
          {isTranslation && item(<Boxes size={12} />, 'Modules', onModules)}
          {isTranslation && item(<Code size={12} />, 'API Logs', onApiLogs)}
          {item(<Edit2 size={12} />, 'Edit Book', onEdit)}
          {item(<Trash2 size={12} />, 'Delete', onDelete, 'text-rose-400 hover:text-rose-300')}
        </div>
      )}
    </div>
  )
}
