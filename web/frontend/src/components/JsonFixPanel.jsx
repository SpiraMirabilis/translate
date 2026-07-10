/**
 * JsonFixPanel
 *
 * Modal overlay that appears when a translation chunk returns malformed JSON.
 * User can retry the chunk, manually fix the JSON, or abort translation.
 */
import { useState, useEffect, useCallback, lazy, Suspense } from 'react'
const JsonCodeMirror = lazy(() => import('./JsonCodeMirror'))
import { api } from '../services/api'
import { RefreshCw, Check, X, ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react'

export default function JsonFixPanel({ rawResponse, chunkIndex, totalChunks, chunkText, isEmpty, timeoutSeconds, onDone }) {
  const [editedJson, setEditedJson] = useState(rawResponse || '')
  const [isValid, setIsValid] = useState(false)
  const [validationError, setValidationError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  const [showSource, setShowSource] = useState(false)
  const [responseEmpty, setResponseEmpty] = useState(isEmpty || false)
  const [remaining, setRemaining] = useState(
    Number.isFinite(timeoutSeconds) && timeoutSeconds > 0 ? Math.round(timeoutSeconds) : null
  )

  // Always fetch raw_response from API — the WS message triggers the modal
  // but the payload can get lost/truncated in transit.
  useEffect(() => {
    api.getJobStatus().then(d => {
      if (d.pending_json_fix) {
        const fix = d.pending_json_fix
        if (fix.raw_response) setEditedJson(fix.raw_response)
        if (fix.is_empty) setResponseEmpty(true)
        if (remaining === null && fix.timeout_seconds > 0) {
          setRemaining(Math.round(fix.timeout_seconds))
        }
      }
    }).catch(() => {})
  }, [rawResponse])

  // Validate JSON on every edit
  useEffect(() => {
    try {
      JSON.parse(editedJson)
      setIsValid(true)
      setValidationError('')
    } catch (e) {
      setIsValid(false)
      setValidationError(e.message)
    }
  }, [editedJson])

  // Auto-dismiss countdown. The backend stops waiting after `timeoutSeconds`
  // and defaults to retrying the chunk, so when our timer hits zero we just
  // close the modal locally (the backend also pushes `json_fix_resolved`).
  // Pauses while a manual submit is in flight.
  useEffect(() => {
    if (remaining === null || submitting) return
    if (remaining <= 0) {
      onDone()
      return
    }
    const t = setTimeout(() => setRemaining(r => (r === null ? null : r - 1)), 1000)
    return () => clearTimeout(t)
  }, [remaining, submitting, onDone])

  const submit = useCallback(async (action) => {
    setSubmitting(true)
    setSubmitError(null)
    try {
      const body = { action }
      if (action === 'fix') body.json = editedJson
      await api.submitJsonFix(body)
      onDone()
    } catch (e) {
      console.error('JSON fix submit failed:', e)
      setSubmitError(e.message || 'Submit failed.')
    } finally {
      setSubmitting(false)
    }
  }, [editedJson, onDone])

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-slate-700 shrink-0">
          <AlertTriangle size={18} className="text-amber-400" />
          <h2 className="text-sm font-semibold text-slate-200 flex-1">JSON Fix Required</h2>
          {remaining !== null && (
            <span
              className="text-xs text-slate-400 tabular-nums"
              title="Auto-retries this chunk if no action is taken"
            >
              Auto-retry in {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')}
            </span>
          )}
          <span className="badge-amber text-xs">Chunk {chunkIndex}/{totalChunks}</span>
        </div>

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4 min-h-0">
          {/* Collapsible source context */}
          {chunkText && (
            <div>
              <button
                className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-300"
                onClick={() => setShowSource(s => !s)}
              >
                {showSource ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                Source text being translated
              </button>
              {showSource && (
                <pre className="mt-2 p-3 bg-slate-800 rounded text-xs text-slate-400 max-h-32 overflow-y-auto whitespace-pre-wrap">
                  {chunkText}
                </pre>
              )}
            </div>
          )}

          {/* Empty response warning */}
          {responseEmpty && (
            <div className="flex items-center gap-2 p-3 bg-amber-900/30 border border-amber-700/50 rounded-lg text-xs text-amber-300">
              <AlertTriangle size={14} className="shrink-0" />
              <span>The model returned an empty response after all retry attempts. You can retry the chunk or abort.</span>
            </div>
          )}

          {/* JSON editor */}
          <div>
            <label className="label mb-1.5">AI response (edit to fix)</label>
            <div className="rounded-lg overflow-hidden border border-slate-700">
              <Suspense fallback={<div className="p-4 text-slate-400 text-sm">Loading editor…</div>}>
                <JsonCodeMirror
                  value={editedJson}
                  onChange={(val) => setEditedJson(val)}
                  minHeight="200px"
                  maxHeight="400px"
                />
              </Suspense>
            </div>
          </div>

          {/* Validation indicator */}
          <div className="flex items-center gap-2 text-xs">
            {isValid ? (
              <>
                <Check size={14} className="text-emerald-400" />
                <span className="text-emerald-400">Valid JSON</span>
              </>
            ) : (
              <>
                <X size={14} className="text-rose-400" />
                <span className="text-rose-400">{validationError}</span>
              </>
            )}
          </div>
        </div>

        {/* Submit failure */}
        {submitError && (
          <div className="flex items-center gap-2 px-5 py-2 border-t border-slate-700 text-xs text-rose-400 shrink-0">
            <X size={14} className="shrink-0" />
            <span>Failed to submit: {submitError}</span>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3 px-5 py-4 border-t border-slate-700 shrink-0">
          <button
            className="btn-secondary flex items-center gap-1.5"
            onClick={() => submit('retry')}
            disabled={submitting}
          >
            <RefreshCw size={13} /> Retry Chunk
          </button>
          <button
            className="btn-primary flex items-center gap-1.5 flex-1"
            onClick={() => submit('fix')}
            disabled={submitting || !isValid}
          >
            <Check size={13} /> Submit Fix
          </button>
          <button
            className="btn-ghost text-rose-400 hover:text-rose-300"
            onClick={() => submit('abort')}
            disabled={submitting}
          >
            Abort
          </button>
        </div>
      </div>
    </div>
  )
}
