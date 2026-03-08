/**
 * TranslationProgress
 *
 * Shows a progress bar + stats while a translation is running.
 * Driven by WebSocket progress messages from the backend.
 *
 * Expected message shape:
 *   { type: "progress", chunk, total, phase, token_count, expected_tokens, percent, tokens_per_second, elapsed }
 */
export default function TranslationProgress({ progress, status }) {
  if (!progress && status === 'running') {
    // Job started but no progress message yet
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>Starting…</span>
        </div>
        <div className="h-2 rounded-full bg-slate-700 overflow-hidden">
          <div className="h-full bg-indigo-500 rounded-full animate-pulse w-1/6" />
        </div>
      </div>
    )
  }

  if (!progress) return null

  const { chunk, total, phase, token_count, expected_tokens, percent, tokens_per_second, elapsed } = progress
  const hasTokenData = token_count != null && expected_tokens != null

  // Indeterminate pulse when chunk just started (phase === "start")
  const isIndeterminate = phase === 'start' || !hasTokenData

  return (
    <div className="space-y-2">
      {/* Labels row */}
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span>
          Chunk {chunk}/{total}
          {hasTokenData && (
            <span className="ml-2 text-slate-500">
              {token_count.toLocaleString()} / {expected_tokens.toLocaleString()} tokens (estimated)
            </span>
          )}
        </span>
        <span className="flex items-center gap-3">
          {tokens_per_second != null && (
            <span className="text-slate-500">{tokens_per_second} tok/s</span>
          )}
          {elapsed != null && (
            <span className="text-slate-500">{elapsed}s</span>
          )}
          {hasTokenData && !isIndeterminate && (
            <span className="text-indigo-400 font-medium">{percent}%</span>
          )}
        </span>
      </div>

      {/* Progress bar */}
      <div className="h-2 rounded-full bg-slate-700 overflow-hidden">
        {isIndeterminate ? (
          <div className="h-full bg-indigo-500 rounded-full w-12 animate-[slide_1.2s_ease-in-out_infinite]"
               style={{ animation: 'indeterminate 1.4s ease-in-out infinite' }} />
        ) : (
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-300"
            style={{ width: `${Math.min(100, percent || 0)}%` }}
          />
        )}
      </div>

      {/* Multi-chunk overview dots */}
      {total > 1 && (
        <div className="flex items-center gap-1 mt-1">
          {Array.from({ length: total }, (_, i) => {
            const chunkNum = i + 1
            let cls = 'w-2 h-2 rounded-full '
            if (chunkNum < chunk) cls += 'bg-emerald-500'
            else if (chunkNum === chunk) cls += 'bg-indigo-400 ring-2 ring-indigo-400/30'
            else cls += 'bg-slate-600'
            return <div key={i} className={cls} title={`Chunk ${chunkNum}`} />
          })}
          <span className="text-xs text-slate-500 ml-1">
            {chunk < total ? `${total - chunk} chunk${total - chunk > 1 ? 's' : ''} remaining` : 'Last chunk'}
          </span>
        </div>
      )}
    </div>
  )
}
