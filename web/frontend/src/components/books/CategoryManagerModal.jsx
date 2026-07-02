import { useState, useEffect } from 'react'
import { api } from '../../services/api'
import { Plus, Trash2, X, Loader2 } from 'lucide-react'
import { DEFAULT_CATEGORIES, catBadgeProps } from '../../utils/categories'
import { useTransientFlag } from '../../hooks/useTransientFlag'

export default function CategoryManagerModal({ book, onClose }) {
  const [categories, setCategories] = useState([])
  const [attributes, setAttributes] = useState({})
  const [loading, setLoading] = useState(true)
  const [isDefault, setIsDefault] = useState(true)
  const [newCat, setNewCat] = useState('')
  const [entityCounts, setEntityCounts] = useState({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [successMsg, flashSuccessMsg, clearSuccessMsg] = useTransientFlag(3000)

  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const [catData, countData] = await Promise.all([
          api.getBookCategories(book.id),
          api.getCategoryEntityCounts(book.id),
        ])
        setCategories(catData.categories || DEFAULT_CATEGORIES)
        setAttributes(catData.attributes || {})
        setIsDefault(catData.is_default)
        setEntityCounts(countData.counts || {})
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    })()
  }, [book.id])

  const handleSave = async (cats, attrs) => {
    setSaving(true); setError(null); clearSuccessMsg()
    try {
      const res = await api.setBookCategories(book.id, { categories: cats, attributes: attrs })
      setCategories(res.categories)
      setAttributes(res.attributes || {})
      setIsDefault(false)
      flashSuccessMsg('Categories saved.')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleAdd = () => {
    const c = newCat.trim().toLowerCase()
    if (!c) return
    if (categories.includes(c)) { setError(`"${c}" already exists.`); return }
    setError(null)
    const updated = [...categories, c]
    setCategories(updated)
    setNewCat('')
    handleSave(updated, attributes)
  }

  const handleRemove = (cat) => {
    const count = entityCounts[cat] || 0
    if (count > 0 && !confirm(`"${cat}" has ${count} entities. They won't be deleted but will be hidden from translation prompts and UI filters. Continue?`)) return
    const updated = categories.filter(c => c !== cat)
    if (updated.length === 0) { setError('At least one category is required.'); return }
    const { [cat]: _removed, ...restAttrs } = attributes
    setCategories(updated)
    setAttributes(restAttrs)
    handleSave(updated, restAttrs)
  }

  const handleToggleGender = (cat) => {
    const current = attributes[cat] || []
    const next = current.includes('gender')
      ? current.filter(a => a !== 'gender')
      : [...current, 'gender']
    const updated = { ...attributes, [cat]: next }
    setAttributes(updated)
    handleSave(categories, updated)
  }

  const handleReset = async () => {
    if (!confirm('Reset to default categories? Custom categories will be removed (entities are preserved).')) return
    setSaving(true); setError(null); clearSuccessMsg()
    try {
      await api.resetBookCategories(book.id)
      setCategories([...DEFAULT_CATEGORIES])
      setAttributes({ characters: ['gender'] })
      setIsDefault(true)
      flashSuccessMsg('Reset to defaults.')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-slate-200">Entity Categories</h2>
            <p className="text-xs text-slate-500 mt-0.5">{book.title}</p>
          </div>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Loader2 size={14} className="animate-spin" /> Loading...
          </div>
        ) : (
          <>
            <div className="space-y-1.5">
              {categories.map(cat => {
                const gendered = (attributes[cat] || []).includes('gender')
                return (
                <div key={cat} className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-slate-750/50">
                  <div className="flex items-center gap-2">
                    <span {...catBadgeProps(cat)}>{cat}</span>
                    {entityCounts[cat] > 0 && (
                      <span className="text-xs text-slate-500">{entityCounts[cat]} entities</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      className={`text-xs px-1.5 py-0.5 rounded border transition-colors ${
                        gendered
                          ? 'text-indigo-300 bg-indigo-900/50 border-indigo-500'
                          : 'text-slate-500 border-slate-600 hover:text-slate-300'
                      }`}
                      title={gendered
                        ? 'Gender-tracked — entities in this category carry a gender (enables pronoun repair). Click to disable.'
                        : 'Click to gender-track this category (adds a gender field + enables pronoun repair).'}
                      onClick={() => handleToggleGender(cat)}
                      disabled={saving}
                    >
                      ⚥ gender
                    </button>
                    <button
                      className="btn-ghost p-1 hover:text-rose-400"
                      title="Remove category"
                      onClick={() => handleRemove(cat)}
                      disabled={saving}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              )})}
            </div>

            <div className="flex gap-2">
              <input
                className="input flex-1"
                placeholder="New category name..."
                value={newCat}
                onChange={e => setNewCat(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAdd()}
              />
              <button
                className="btn-primary flex items-center gap-1"
                onClick={handleAdd}
                disabled={saving || !newCat.trim()}
              >
                <Plus size={13} /> Add
              </button>
            </div>

            {(error || successMsg) && (
              <div>
                {error && <p className="text-rose-400 text-sm">{error}</p>}
                {successMsg && <p className="text-emerald-400 text-sm">{successMsg}</p>}
              </div>
            )}

            <div className="flex items-center justify-between pt-2 border-t border-slate-700">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                {isDefault ? 'Using defaults' : 'Custom categories'}
              </div>
              <div className="flex gap-2">
                {!isDefault && (
                  <button className="btn-secondary text-xs" onClick={handleReset} disabled={saving}>
                    Reset to Defaults
                  </button>
                )}
                <button className="btn-secondary" onClick={onClose}>Close</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
