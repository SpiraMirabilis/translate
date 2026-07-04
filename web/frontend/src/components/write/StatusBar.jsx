import { useState } from 'react'
import { Target, Check, CloudUpload, CircleAlert } from 'lucide-react'

/**
 * Bottom status bar: live word count, session delta, daily-goal progress,
 * and save state. Goal is stored in localStorage by the parent.
 */
export default function StatusBar({ words, sessionWords, dailyWords, dailyGoal,
  onSetGoal, dirty, saving, savedFlash, autosavedFlash, conflict }) {
  const [editingGoal, setEditingGoal] = useState(false)
  const [goalInput, setGoalInput] = useState(dailyGoal || '')

  const commitGoal = () => {
    const n = parseInt(goalInput, 10)
    onSetGoal(Number.isFinite(n) && n > 0 ? n : null)
    setEditingGoal(false)
  }

  const pct = dailyGoal ? Math.min(100, Math.round((dailyWords / dailyGoal) * 100)) : null

  return (
    <div className="flex items-center gap-4 px-4 py-1.5 text-xs text-slate-500 border-t border-slate-800 bg-slate-900/80 rounded-b-lg">
      <span className="tabular-nums text-slate-400">{words.toLocaleString()} words</span>
      <span className="tabular-nums" title="Words written this session">
        session {sessionWords >= 0 ? '+' : ''}{sessionWords.toLocaleString()}
      </span>

      {/* Daily goal */}
      <div className="flex items-center gap-1.5">
        <Target size={11} />
        {editingGoal ? (
          <input
            autoFocus
            className="w-20 bg-slate-800 border border-slate-700 rounded px-1.5 py-0.5 text-xs text-slate-200 outline-none"
            value={goalInput}
            placeholder="words/day"
            onChange={(e) => setGoalInput(e.target.value.replace(/\D/g, ''))}
            onBlur={commitGoal}
            onKeyDown={(e) => { if (e.key === 'Enter') commitGoal(); if (e.key === 'Escape') setEditingGoal(false) }}
          />
        ) : (
          <button
            type="button"
            className="hover:text-slate-300"
            title="Set a daily word goal"
            onClick={() => { setGoalInput(dailyGoal || ''); setEditingGoal(true) }}
          >
            {dailyGoal
              ? <span className="tabular-nums">today {dailyWords.toLocaleString()} / {dailyGoal.toLocaleString()}</span>
              : 'set goal'}
          </button>
        )}
        {pct !== null && (
          <div className="w-24 h-1.5 rounded bg-slate-800 overflow-hidden" title={`${pct}% of daily goal`}>
            <div
              className={`h-full ${pct >= 100 ? 'bg-emerald-500' : 'bg-indigo-500'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
      </div>

      <div className="flex-1" />

      {/* Save state */}
      {conflict ? (
        <span className="flex items-center gap-1 text-rose-400"><CircleAlert size={11} /> conflict</span>
      ) : saving ? (
        <span className="flex items-center gap-1 text-slate-400"><CloudUpload size={11} /> saving…</span>
      ) : savedFlash ? (
        <span className="flex items-center gap-1 text-emerald-400"><Check size={11} /> saved</span>
      ) : autosavedFlash ? (
        <span className="flex items-center gap-1 text-slate-400"><CloudUpload size={11} /> autosaved</span>
      ) : dirty ? (
        <span className="text-amber-400/80">unsaved changes</span>
      ) : (
        <span>saved</span>
      )}
    </div>
  )
}
