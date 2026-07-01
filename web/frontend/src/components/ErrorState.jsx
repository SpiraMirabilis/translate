import { AlertCircle } from 'lucide-react'

/**
 * Shared error display with an optional Retry button.
 *
 *   size='page'   — full-page centered block (replaces a content area)
 *   size='inline' — compact banner for embedding inside a panel/toolbar
 *
 * Colors default to the admin slate palette. Pages with their own theming
 * (e.g. the Reader's light/sepia/dark themes) can pass `className` for the
 * text color and `buttonClassName` for the Retry button.
 */
export default function ErrorState({
  message = 'Something went wrong',
  detail = null,
  onRetry = null,
  retryLabel = 'Retry',
  size = 'inline',
  className = '',
  buttonClassName = '',
}) {
  const btnCls = `rounded-lg border text-sm transition-colors ${
    buttonClassName || 'border-slate-600 text-slate-300 hover:bg-slate-800'
  }`

  if (size === 'page') {
    return (
      <div className={`flex flex-col items-center justify-center py-32 text-center px-6 ${className}`}>
        <p className="text-lg mb-2">{message}</p>
        {detail && <p className="text-sm opacity-70 mb-6 max-w-md">{detail}</p>}
        {onRetry && (
          <button onClick={onRetry} className={`px-4 py-2 ${btnCls}`}>
            {retryLabel}
          </button>
        )}
      </div>
    )
  }

  return (
    <div className={`flex items-center gap-3 px-4 py-2.5 rounded border border-rose-900 bg-rose-950/40 text-sm ${className}`}>
      <AlertCircle size={16} className="text-rose-400 shrink-0" />
      <div className="flex-1 min-w-0">
        <span className="text-rose-300">{message}</span>
        {detail && <span className="text-rose-300/70 ml-2">{detail}</span>}
      </div>
      {onRetry && (
        <button onClick={onRetry} className={`px-3 py-1 shrink-0 ${btnCls}`}>
          {retryLabel}
        </button>
      )}
    </div>
  )
}
