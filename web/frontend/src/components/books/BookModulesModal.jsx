import { useState, useEffect } from 'react'
import { api } from '../../services/api'
import { X, Check, Loader2, Settings } from 'lucide-react'

export default function BookModulesModal({ book, onClose, onSaved }) {
  const [available, setAvailable] = useState([])
  const [overrides, setOverrides] = useState(
    (book?.modules && typeof book.modules === 'object') ? book.modules : {}
  )
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [settingsFor, setSettingsFor] = useState(null)  // module whose settings modal is open

  const reloadModules = async () => {
    const d = await api.getModules(book.id)
    setAvailable(d.modules || [])
  }

  useEffect(() => {
    (async () => {
      try {
        await reloadModules()
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const stateOf = (id) => overrides?.[id] === true ? 'on' : overrides?.[id] === false ? 'off' : 'auto'
  const setState = (id, state) => {
    setOverrides(o => {
      const next = { ...(o || {}) }
      if (state === 'auto') delete next[id]
      else next[id] = (state === 'on')
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await api.updateBook(book.id, { modules: overrides })
      onSaved()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-2xl max-w-[95vw] max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div>
            <h2 className="font-semibold text-slate-200">Modules — {book.title}</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              <span className="font-medium">Auto</span> matches the book&apos;s Source URL against each module&apos;s site.
              Set <span className="font-medium">On</span>/<span className="font-medium">Off</span> to override for this book.
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
            <div className="flex-1 overflow-auto p-4 space-y-3">
              {available.length === 0 && (
                <p className="text-sm text-slate-500">No modules are registered.</p>
              )}
              {available.map(m => {
                // Modules with no auto-on behavior (no default_enabled, URL match,
                // or custom auto-rule) get no "Auto" option and default to Off.
                const hasAuto = m.has_auto !== false
                const value = hasAuto ? stateOf(m.id) : (overrides?.[m.id] === true ? 'on' : 'off')
                const onChange = (e) => {
                  const v = e.target.value
                  // For no-auto modules "Off" == the auto default, so clear the override.
                  setState(m.id, (!hasAuto && v === 'off') ? 'auto' : v)
                }
                // Whether the module's auto-trigger currently fires for this book
                // (server-resolved). Only meaningful while the dropdown is on Auto.
                const autoResolved = m.auto_enabled === true
                const autoKnown = m.auto_enabled === true || m.auto_enabled === false
                const showAutoState = value === 'auto' && autoKnown
                return (
                  <div key={m.id} className="flex items-start gap-3 border-b border-slate-800 pb-3 last:border-0">
                    <select
                      className={`input w-24 shrink-0${showAutoState ? (autoResolved ? ' text-emerald-400' : ' text-slate-500') : ''}`}
                      value={value}
                      onChange={onChange}
                    >
                      {hasAuto && <option value="auto">Auto</option>}
                      <option value="on">On</option>
                      <option value="off">Off</option>
                    </select>
                    <div className="text-sm min-w-0 flex-1">
                      <div className="text-slate-200">
                        {m.name} <span className="text-xs text-slate-500">({m.id})</span>
                        {showAutoState && (
                          <span className={`ml-2 text-[11px] px-1.5 py-0.5 rounded ${autoResolved ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-700/60 text-slate-400'}`}>
                            {autoResolved ? 'Auto → enabled' : 'Auto → off'}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-slate-500">{m.description}</div>
                      {hasAuto && m.auto_hint && (
                        <div className="text-[11px] text-slate-600 mt-0.5">Auto: {m.auto_hint}</div>
                      )}
                    </div>
                    {m.has_settings && (
                      <button
                        className="btn-ghost p-1 shrink-0 self-center"
                        title="Module settings"
                        onClick={() => setSettingsFor(m)}
                      >
                        <Settings size={15} />
                      </button>
                    )}
                  </div>
                )
              })}
            </div>

            {error && <div className="px-5 shrink-0"><p className="text-rose-400 text-sm">{error}</p></div>}

            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-slate-700 shrink-0">
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                Save
              </button>
            </div>
          </>
        )}
      </div>

      {settingsFor && (
        <ModuleSettingsModal
          book={book}
          module={settingsFor}
          onClose={() => setSettingsFor(null)}
          onSaved={async () => { setSettingsFor(null); try { await reloadModules() } catch { /* keep prior list */ } }}
        />
      )}
    </div>
  )
}

// Schema-driven per-book settings for one module. Stacked at z-[60] above the
// modules modal (mirrors the EntityFormModal → DeleteEntityModal precedent).
// Fields render from module.settings_schema; a field is shown/saved only when
// every key in its optional `show_if` equals the current form value.
function ModuleSettingsModal({ book, module, onClose, onSaved }) {
  const schema = module.settings_schema || []
  const [form, setForm] = useState(() => ({ ...(module.settings || {}) }))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [catOptions, setCatOptions] = useState(null)

  const needsCategories = schema.some(f => f.options_source === 'book_categories')
  useEffect(() => {
    if (!needsCategories) return
    (async () => {
      try {
        const d = await api.getBookCategories(book.id)
        setCatOptions(d.categories || [])
      } catch { setCatOptions([]) }
    })()
  }, [])

  const visible = (f) => !f.show_if || Object.entries(f.show_if).every(([k, v]) => form[k] === v)
  const setField = (key, value) => setForm(s => ({ ...s, [key]: value }))
  const optionsFor = (f) => f.options_source === 'book_categories' ? (catOptions || []) : (f.options || [])

  const handleSave = async () => {
    setSaving(true); setError(null)
    // Strip hidden fields so stale values from a since-hidden branch aren't saved.
    const out = {}
    for (const f of schema) if (visible(f)) out[f.key] = form[f.key]
    try {
      await api.setModuleSettings(book.id, module.id, out)
      onSaved(out)
    } catch (e) { setError(e.message); setSaving(false) }
  }

  const renderField = (f) => {
    const value = form[f.key]
    if (f.type === 'bool') {
      return (
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={!!value} onChange={e => setField(f.key, e.target.checked)} />
          <span className="text-sm text-slate-200">{f.label}</span>
        </label>
      )
    }
    if (f.type === 'multiselect') {
      const opts = optionsFor(f)
      const sel = Array.isArray(value) ? value : []
      const toggle = (opt) => setField(f.key, sel.includes(opt) ? sel.filter(x => x !== opt) : [...sel, opt])
      return (
        <div>
          <div className="label">{f.label}</div>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {opts.length === 0 && <span className="text-xs text-slate-500">No categories available.</span>}
            {opts.map(opt => (
              <button type="button" key={opt} onClick={() => toggle(opt)}
                className={`text-xs px-2 py-1 rounded border ${sel.includes(opt) ? 'bg-emerald-500/15 text-emerald-300 border-emerald-600' : 'border-slate-700 text-slate-400 hover:border-slate-500'}`}>
                {opt}
              </button>
            ))}
          </div>
        </div>
      )
    }
    if (f.type === 'select') {
      return (
        <div>
          <div className="label">{f.label}</div>
          <select className="input mt-1" value={value ?? ''} onChange={e => setField(f.key, e.target.value)}>
            {optionsFor(f).map(opt => <option key={opt} value={opt}>{opt}</option>)}
          </select>
        </div>
      )
    }
    if (f.type === 'textarea') {
      return (
        <div>
          <div className="label">{f.label}</div>
          <textarea className="input mt-1" rows={4} value={value ?? ''} onChange={e => setField(f.key, e.target.value)} />
        </div>
      )
    }
    return (
      <div>
        <div className="label">{f.label}</div>
        <input className="input mt-1" type={f.type === 'number' ? 'number' : 'text'}
          value={value ?? ''}
          onChange={e => setField(f.key, f.type === 'number' ? Number(e.target.value) : e.target.value)} />
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="card w-full max-w-lg max-w-[95vw] max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <h2 className="font-semibold text-slate-200">{module.name} — Settings</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-auto p-5 space-y-4">
          {schema.length === 0 && <p className="text-sm text-slate-500">This module has no settings.</p>}
          {schema.filter(visible).map(f => (
            <div key={f.key}>
              {renderField(f)}
              {f.help && <p className="text-[11px] text-slate-500 mt-1">{f.help}</p>}
            </div>
          ))}
        </div>
        {error && <div className="px-5 shrink-0"><p className="text-rose-400 text-sm">{error}</p></div>}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-slate-700 shrink-0">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
