import { X, Minus, Plus } from 'lucide-react'

const THEMES = [
  { id: 'light', label: 'Light', bg: 'bg-stone-50',  ring: 'ring-stone-300',  text: 'text-gray-800'  },
  { id: 'sepia', label: 'Sepia', bg: 'bg-amber-50',  ring: 'ring-amber-300',  text: 'text-amber-900' },
  { id: 'dark',  label: 'Dark',  bg: 'bg-slate-800', ring: 'ring-slate-500', text: 'text-slate-200' },
]

const FONTS = [
  { id: 'serif', label: 'Serif',  sample: 'Georgia, serif' },
  { id: 'sans',  label: 'Sans',   sample: 'system-ui, sans-serif' },
  { id: 'mono',  label: 'Mono',   sample: '"JetBrains Mono", monospace' },
]

const MARGINS = [
  { id: 'narrow', label: 'Narrow' },
  { id: 'medium', label: 'Medium' },
  { id: 'wide',   label: 'Wide'   },
]

export default function ReaderSettings({ open, onClose, prefs, setPrefs, hasSource }) {
  if (!open) return null

  const isDark = prefs.theme === 'dark'
  const panelBg = isDark ? 'bg-slate-800' : 'bg-white'
  const borderColor = isDark ? 'border-slate-700' : 'border-stone-200'
  const textPrimary = isDark ? 'text-slate-100' : 'text-gray-900'
  const textSecondary = isDark ? 'text-slate-400' : 'text-gray-500'

  const update = (key, value) => setPrefs(p => ({ ...p, [key]: value }))

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />
      <div className={`fixed inset-y-0 right-0 z-50 w-80 max-w-[85vw] ${panelBg} border-l ${borderColor} flex flex-col shadow-2xl`}>
        <div className={`p-4 border-b ${borderColor} flex items-center justify-between`}>
          <h2 className={`font-semibold ${textPrimary}`}>Reading Settings</h2>
          <button onClick={onClose} className={`${textSecondary} p-1`}>
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* Theme */}
          <div>
            <label className={`text-xs font-medium ${textSecondary} uppercase tracking-wider`}>Theme</label>
            <div className="flex gap-2 mt-2">
              {THEMES.map(t => (
                <button
                  key={t.id}
                  onClick={() => update('theme', t.id)}
                  className={`flex-1 py-3 rounded-lg border-2 text-center text-sm font-medium transition-all
                    ${t.bg} ${t.text}
                    ${prefs.theme === t.id ? `${t.ring} ring-2` : 'border-transparent opacity-70 hover:opacity-100'}`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Font Size */}
          <div>
            <label className={`text-xs font-medium ${textSecondary} uppercase tracking-wider`}>
              Font Size — {prefs.fontSize}px
            </label>
            <div className="flex items-center gap-3 mt-2">
              <button
                onClick={() => update('fontSize', Math.max(12, prefs.fontSize - 1))}
                className={`w-9 h-9 rounded-lg border ${borderColor} flex items-center justify-center ${textPrimary} hover:bg-black/5`}
              >
                <Minus size={14} />
              </button>
              <input
                type="range"
                min={12} max={32} step={1}
                value={prefs.fontSize}
                onChange={e => update('fontSize', +e.target.value)}
                className="flex-1 accent-indigo-500"
              />
              <button
                onClick={() => update('fontSize', Math.min(32, prefs.fontSize + 1))}
                className={`w-9 h-9 rounded-lg border ${borderColor} flex items-center justify-center ${textPrimary} hover:bg-black/5`}
              >
                <Plus size={14} />
              </button>
            </div>
          </div>

          {/* Font Family */}
          <div>
            <label className={`text-xs font-medium ${textSecondary} uppercase tracking-wider`}>Font</label>
            <div className="flex flex-col gap-1.5 mt-2">
              {FONTS.map(f => (
                <button
                  key={f.id}
                  onClick={() => update('fontFamily', f.id)}
                  className={`text-left px-3 py-2.5 rounded-lg border transition-all text-sm
                    ${prefs.fontFamily === f.id
                      ? `border-indigo-500 ${isDark ? 'bg-indigo-500/10 text-indigo-300' : 'bg-indigo-50 text-indigo-700'}`
                      : `${borderColor} ${textPrimary} hover:border-indigo-400/50`}`}
                  style={{ fontFamily: f.sample }}
                >
                  {f.label} — The quick brown fox jumps over the lazy dog
                </button>
              ))}
            </div>
          </div>

          {/* Line Height */}
          <div>
            <label className={`text-xs font-medium ${textSecondary} uppercase tracking-wider`}>
              Line Spacing — {prefs.lineHeight.toFixed(1)}
            </label>
            <input
              type="range"
              min={1.2} max={2.6} step={0.1}
              value={prefs.lineHeight}
              onChange={e => update('lineHeight', +e.target.value)}
              className="w-full mt-2 accent-indigo-500"
            />
            <div className={`flex justify-between text-xs ${textSecondary} mt-1`}>
              <span>Compact</span>
              <span>Spacious</span>
            </div>
          </div>

          {/* Margins */}
          <div>
            <label className={`text-xs font-medium ${textSecondary} uppercase tracking-wider`}>Margins</label>
            <div className="flex gap-2 mt-2">
              {MARGINS.map(m => (
                <button
                  key={m.id}
                  onClick={() => update('margins', m.id)}
                  className={`flex-1 py-2 rounded-lg border text-sm font-medium transition-all
                    ${prefs.margins === m.id
                      ? `border-indigo-500 ${isDark ? 'bg-indigo-500/10 text-indigo-300' : 'bg-indigo-50 text-indigo-700'}`
                      : `${borderColor} ${textPrimary} hover:border-indigo-400/50`}`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          {/* Source / Translation toggle */}
          {hasSource && (
            <div>
              <label className={`text-xs font-medium ${textSecondary} uppercase tracking-wider`}>Content</label>
              <div className="flex gap-2 mt-2">
                {[
                  { id: 'translated', label: 'Translation' },
                  { id: 'source',     label: 'Source' },
                  { id: 'both',       label: 'Both' },
                ].map(opt => (
                  <button
                    key={opt.id}
                    onClick={() => update('contentMode', opt.id)}
                    className={`flex-1 py-2 rounded-lg border text-sm font-medium transition-all
                      ${(prefs.contentMode || 'translated') === opt.id
                        ? `border-indigo-500 ${isDark ? 'bg-indigo-500/10 text-indigo-300' : 'bg-indigo-50 text-indigo-700'}`
                        : `${borderColor} ${textPrimary} hover:border-indigo-400/50`}`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
