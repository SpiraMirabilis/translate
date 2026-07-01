import { useState, useEffect } from 'react'
import { api } from '../../services/api'
import { X, Check, Loader2 } from 'lucide-react'
import { useTransientFlag } from '../../hooks/useTransientFlag'

export default function PromptEditorModal({ book, onClose }) {
  const [template, setTemplate] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [hasCustom, setHasCustom] = useState(false)
  const [error, setError] = useState(null)
  const [successMsg, flashSuccessMsg, clearSuccessMsg] = useTransientFlag(3000)

  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const d = await api.getPrompt(book.id)
        if (d.template) {
          setTemplate(d.template)
          setHasCustom(true)
        } else {
          // Load default template as starting point
          const def = await api.getDefaultPrompt()
          setTemplate(def.template || '')
          setHasCustom(false)
        }
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    })()
  }, [book.id])

  const handleSave = async () => {
    if (!template.includes('{{ENTITIES_JSON}}')) {
      setError('Template must contain the {{ENTITIES_JSON}} placeholder.')
      return
    }
    setSaving(true); setError(null); clearSuccessMsg()
    try {
      await api.setPrompt(book.id, { template })
      setHasCustom(true)
      flashSuccessMsg('Prompt saved.')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    if (!confirm('Reset to the default system prompt? Your custom prompt for this book will be deleted.')) return
    setError(null); clearSuccessMsg()
    try {
      await api.resetPrompt(book.id)
      const def = await api.getDefaultPrompt()
      setTemplate(def.template || '')
      setHasCustom(false)
      flashSuccessMsg('Reset to default.')
    } catch (e) {
      setError(e.message)
    }
  }

  const handleLoadDefault = async () => {
    setError(null)
    try {
      const def = await api.getDefaultPrompt()
      setTemplate(def.template || '')
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-4xl max-w-[95vw] max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-200">System Prompt — {book.title}</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              {hasCustom
                ? 'This book has a custom system prompt.'
                : 'Using the default system prompt. Save to create a custom one for this book.'}
            </p>
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center p-12 text-slate-400 text-sm">
            <Loader2 size={14} className="animate-spin mr-2" /> Loading…
          </div>
        ) : (
          <>
            <div className="flex-1 overflow-hidden p-4">
              <p className="text-xs text-slate-500 mb-2">
                Use <code className="px-1 py-0.5 bg-slate-700 rounded text-slate-300">{'{{ENTITIES_JSON}}'}</code> where
                the entity list should be inserted at translation time.
              </p>
              <textarea
                className="input w-full h-full min-h-[400px] font-mono text-xs leading-relaxed resize-none"
                value={template}
                onChange={e => setTemplate(e.target.value)}
                spellCheck={false}
              />
            </div>

            {(error || successMsg) && (
              <div className="px-5 shrink-0">
                {error && <p className="text-rose-400 text-sm">{error}</p>}
                {successMsg && <p className="text-emerald-400 text-sm">{successMsg}</p>}
              </div>
            )}

            <div className="flex items-center justify-between px-5 py-3 border-t border-slate-700 shrink-0">
              <div className="flex gap-2">
                <button className="btn-secondary text-xs" onClick={handleLoadDefault}>
                  Load default template
                </button>
                {hasCustom && (
                  <button className="btn-danger text-xs" onClick={handleReset}>
                    Reset to default
                  </button>
                )}
              </div>
              <div className="flex gap-2">
                <button className="btn-secondary" onClick={onClose}>Cancel</button>
                <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                  Save
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
