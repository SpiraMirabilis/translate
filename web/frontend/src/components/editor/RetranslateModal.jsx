import { useState, useEffect, useRef } from 'react'
import { api } from '../../services/api'
import ComboBox from '../ComboBox'
import { useLocalStorage } from '../../hooks/useLocalStorage'
import { Languages, Loader2, X } from 'lucide-react'

// ── Retranslation Modal ──────────────────────────────────────────────
export default function RetranslateModal({ chineseText, lineIndex, allLines, bookId, providers, onResult, onClose }) {
  const [model, setModel] = useLocalStorage('editor.retranslateModel', '')
  const [translating, setTranslating] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const ref = useRef(null)
  const isWholeLine = lineIndex != null

  const modelOptions = []
  if (providers) {
    for (const p of providers) {
      // CLI-auth providers (e.g. claudecode) have no API key env var at all —
      // show them regardless of has_key (mirrors Settings' cliAuth logic).
      const cliAuth = !p.api_key_env
      if (!cliAuth && !p.has_key) continue
      for (const m of (p.models || [])) {
        modelOptions.push(`${p.name}:${m}`)
      }
      if (p.models?.length === 0 && p.default_model) {
        modelOptions.push(`${p.name}:${p.default_model}`)
      }
    }
  }

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const handleTranslate = async () => {
    if (!model) return
    setTranslating(true)
    setError(null)
    setResult(null)
    try {
      const idx = lineIndex ?? 0
      const contextBefore = allLines.slice(Math.max(0, idx - 3), idx)
      const contextAfter = allLines.slice(idx + 1, idx + 4)
      const res = await api.retranslate({
        text: chineseText,
        context_before: contextBefore,
        context_after: contextAfter,
        model,
        book_id: bookId ? parseInt(bookId) : null,
      })
      setResult(res.translation)
      onResult(chineseText, res.translation, lineIndex)
    } catch (e) {
      setError(e.message)
    } finally {
      setTranslating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div ref={ref} className="bg-slate-900 border border-slate-700 rounded-lg shadow-2xl w-[500px] max-w-[90vw]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Languages size={16} className="text-emerald-400" />
            <span className="text-sm font-medium text-slate-200">
              {isWholeLine ? `Retranslate Line ${lineIndex + 1}` : 'Retranslate Selection'}
            </span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300">
            <X size={14} />
          </button>
        </div>
        <div className="px-4 py-3 space-y-3">
          <div>
            <div className="text-xs text-slate-600 uppercase tracking-wider mb-1">Source</div>
            <div className="text-sm text-slate-300 bg-slate-950 rounded px-3 py-2 font-mono break-all max-h-24 overflow-y-auto">
              {chineseText}
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-600 uppercase tracking-wider block mb-1">Model</label>
            <ComboBox
              value={model}
              onChange={setModel}
              options={modelOptions}
              placeholder="Select a model..."
            />
          </div>
          <button
            className="btn-primary w-full flex items-center justify-center gap-2"
            onClick={handleTranslate}
            disabled={translating || !model}
          >
            {translating ? (
              <><Loader2 size={14} className="animate-spin" /> Translating...</>
            ) : (
              <><Languages size={14} /> Translate</>
            )}
          </button>
          {error && <p className="text-rose-400 text-xs">{error}</p>}
          {result && (
            <div>
              <div className="text-xs text-slate-600 uppercase tracking-wider mb-1">Result</div>
              <div className="text-sm text-emerald-300 bg-slate-950 rounded px-3 py-2 leading-relaxed">
                {result}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
