import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { api } from '../services/api'
import {
  Search, Plus, Trash2, Edit2, AlertTriangle,
  X, Check, ChevronDown, ChevronUp, ChevronsUpDown, Loader2,
  Pin, CheckSquare, Square, FolderInput, ArrowRightLeft
} from 'lucide-react'
import { DEFAULT_CATEGORIES, catBadgeProps } from '../utils/categories'
import DeleteEntityModal from '../components/DeleteEntityModal'
import EntityFormModal from '../components/EntityFormModal'
import { useUrlState, useUrlModal } from '../hooks/useUrlState'

const TRUNCATE_LIMIT = 25

/**
 * Parse special filter prefixes from a search string.
 * Supported: origin_chapter:N or origin_chapter:N-M
 * Returns { textSearch, originChapterRange: null | [min, max] }
 */
function parseSearchFilters(raw) {
  let textSearch = raw
  let originChapterRange = null
  const m = raw.match(/\borigin_chapter:(\d+)(?:-(\d+))?\b/i)
  if (m) {
    const min = parseInt(m[1])
    const max = m[2] ? parseInt(m[2]) : min
    originChapterRange = [Math.min(min, max), Math.max(min, max)]
    textSearch = raw.replace(m[0], '').trim()
  }
  return { textSearch, originChapterRange }
}

export default function Entities() {
  const [books, setBooks] = useState([])
  const [entities, setEntities] = useState([])
  const [loading, setLoading] = useState(true)

  // Filter/search state lives in the URL as query params so the view is
  // shareable. Replace mode — avoids polluting history with every keystroke.
  const [search, setSearch] = useUrlState('search', '')
  const [filterBook, setFilterBook] = useUrlState('book', '')
  const [filterCat, setFilterCat] = useUrlState('cat', '')
  const [debouncedSearch, setDebouncedSearch] = useState(search)

  // If URL filters are empty on first render but localStorage has a remembered
  // value, block the initial load until the URL has been seeded. Without this
  // gate two loads would fire (empty → all entities, then seeded → filtered),
  // and the slower unfiltered fetch can resolve last and overwrite the correct
  // result.
  const [filtersReady, setFiltersReady] = useState(() => {
    const url = new URLSearchParams(window.location.search)
    const needsBookSeed = !url.get('book') && !!localStorage.getItem('entities_filterBook')
    const needsCatSeed = !url.get('cat') && !!localStorage.getItem('entities_filterCat')
    return !needsBookSeed && !needsCatSeed
  })

  // Seed URL from last-used filter on mount when no URL param is present.
  // URL always wins when it has a value; localStorage is just a per-browser
  // default so navigating away and back doesn't wipe the filter.
  useEffect(() => {
    if (!filterBook) {
      const ls = localStorage.getItem('entities_filterBook')
      if (ls) setFilterBook(ls)
    }
    if (!filterCat) {
      const ls = localStorage.getItem('entities_filterCat')
      if (ls) setFilterCat(ls)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Once the URL reflects the seeded values, release the load gate.
  useEffect(() => {
    if (filtersReady) return
    const lsBook = localStorage.getItem('entities_filterBook') || ''
    const lsCat = localStorage.getItem('entities_filterCat') || ''
    const bookReady = !lsBook || filterBook === lsBook
    const catReady = !lsCat || filterCat === lsCat
    if (bookReady && catReady) setFiltersReady(true)
  }, [filterBook, filterCat, filtersReady])

  // Persist the current filter to localStorage so it survives page navigation.
  useEffect(() => {
    if (filterBook) localStorage.setItem('entities_filterBook', filterBook)
    else localStorage.removeItem('entities_filterBook')
  }, [filterBook])
  useEffect(() => {
    if (filterCat) localStorage.setItem('entities_filterCat', filterCat)
    else localStorage.removeItem('entities_filterCat')
  }, [filterCat])

  // Modals — URL-driven so the back button closes them
  const addModal = useUrlModal('add', {
    paramKeys: ['category', 'untranslated', 'translation', 'book_id'],
  })
  const editEntityModal = useUrlModal('editEntity', { idKey: 'ent' })
  const duplicatesModal = useUrlModal('duplicates')
  const batchModal = useUrlModal('batch', { idKey: 'op' })
  const deleteModal = useUrlModal('delete')

  const prefillEntity = useMemo(() => {
    if (!addModal.isOpen) return null
    const p = {}
    if (addModal.params.category) p.category = addModal.params.category
    if (addModal.params.untranslated) p.untranslated = addModal.params.untranslated
    if (addModal.params.translation) p.translation = addModal.params.translation
    if (addModal.params.book_id) p.book_id = parseInt(addModal.params.book_id, 10)
    return Object.keys(p).length ? p : null
  }, [addModal.isOpen, addModal.params])

  const editingEntity = editEntityModal.isOpen
    ? entities.find(e => String(e.id) === editEntityModal.id) || null
    : null

  const [duplicates, setDuplicates] = useState(null)
  const [error, setError] = useState(null)
  const [activeCategories, setActiveCategories] = useState(DEFAULT_CATEGORIES)
  const [selected, setSelected] = useState(new Set())
  // Local payload for the delete modal (list of entities can't live in URL)
  const [pendingDeletePayload, setPendingDeletePayload] = useState(null)
  const pendingDelete = deleteModal.isOpen ? pendingDeletePayload : null
  const searchRef = useRef(null)

  const toggleSelect = useCallback((id) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }, [])

  const toggleSelectAll = useCallback(() => {
    setSelected(prev => prev.size === entities.length ? new Set() : new Set(entities.map(e => e.id)))
  }, [entities])

  const clearSelection = useCallback(() => setSelected(new Set()), [])

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(timer)
  }, [search])

  // Auto-focus search on mount
  useEffect(() => { searchRef.current?.focus() }, [])

  const { textSearch: apiSearch, originChapterRange } = useMemo(
    () => parseSearchFilters(debouncedSearch),
    [debouncedSearch]
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = {}
      if (filterBook) params.book_id = parseInt(filterBook)
      if (filterCat)  params.category = filterCat
      if (apiSearch) params.search = apiSearch
      const d = await api.listEntities(params)
      let results = d.entities || []
      if (originChapterRange) {
        const [min, max] = originChapterRange
        results = results.filter(e => e.origin_chapter != null && e.origin_chapter >= min && e.origin_chapter <= max)
      }
      setEntities(results)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [filterBook, filterCat, apiSearch, originChapterRange])

  useEffect(() => {
    api.listBooks().then(d => setBooks(d.books || [])).catch(() => {})
  }, [])

  // Fetch book-specific categories when filter changes
  useEffect(() => {
    if (filterBook && filterBook !== 'global') {
      api.getBookCategories(parseInt(filterBook))
        .then(d => setActiveCategories(d.categories || DEFAULT_CATEGORIES))
        .catch(() => setActiveCategories(DEFAULT_CATEGORIES))
    } else {
      setActiveCategories(DEFAULT_CATEGORIES)
    }
  }, [filterBook])

  useEffect(() => {
    if (!filtersReady) return
    load()
    clearSelection()
  }, [load, filtersReady])

  const openDeleteModal = (payload) => {
    setPendingDeletePayload(payload)
    deleteModal.open()
  }
  const closeDeleteModal = () => {
    setPendingDeletePayload(null)
    deleteModal.close()
  }

  const handleDelete = (id) => {
    const ent = entities.find(e => e.id === id)
    if (ent) openDeleteModal({ entities: [ent], mode: 'single' })
  }

  const handleBatchDelete = () => {
    const ents = entities.filter(e => selected.has(e.id))
    if (ents.length) openDeleteModal({ entities: ents, mode: 'batch' })
  }

  const confirmDelete = async (decase) => {
    if (!pendingDelete) return
    const { entities: ents, mode } = pendingDelete
    closeDeleteModal()
    try {
      if (decase) {
        // Group by book_id and decase each unique translation per book
        const seen = new Set()
        for (const e of ents) {
          if (!e.book_id || !e.translation || !/^[A-Z]/.test(e.translation)) continue
          const key = `${e.book_id}::${e.translation}`
          if (seen.has(key)) continue
          seen.add(key)
          await api.decaseEntity({ translation: e.translation, book_id: e.book_id })
        }
      }
      if (mode === 'single') {
        await api.deleteEntity(ents[0].id)
      } else {
        await api.batchEntities({ ids: ents.map(e => e.id), action: 'delete' })
        clearSelection()
      }
      load()
    } catch (e) { setError(e.message) }
  }

  const handleBatchAction = async (action, params) => {
    try {
      await api.batchEntities({ ids: [...selected], action, ...params })
      clearSelection()
      batchModal.close()
      load()
    } catch (e) { setError(e.message) }
  }

  const handleCheckDuplicates = async () => {
    const params = {}
    if (filterBook === 'global') params.scope = 'global'
    else if (filterBook) params.book_id = filterBook
    const d = await api.getDuplicates(Object.keys(params).length ? params : undefined)
    setDuplicates(d)
    duplicatesModal.open()
  }

  // Group entities by category for display (known categories first, then extras)
  const grouped = useMemo(() => {
    const result = {}
    for (const cat of activeCategories) {
      const catEntities = entities.filter(e => e.category === cat)
      if (catEntities.length > 0) result[cat] = catEntities
    }
    // Include any extra categories from entities not in activeCategories
    for (const e of entities) {
      if (!activeCategories.includes(e.category)) {
        if (!result[e.category]) result[e.category] = []
        result[e.category].push(e)
      }
    }
    return result
  }, [entities, activeCategories])

  const notedCount = useMemo(() => entities.filter(e => e.note).length, [entities])

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-5 flex-wrap gap-2">
        <h1 className="text-lg font-semibold text-slate-200">Entities</h1>
        <div className="flex gap-2 flex-wrap">
          <button className="btn-secondary flex items-center gap-1.5 text-xs" onClick={handleCheckDuplicates}>
            <AlertTriangle size={13} /> Check Duplicates
          </button>
          <button className="btn-primary flex items-center gap-1.5" onClick={() => addModal.open()}>
            <Plus size={14} /> Add Entity
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-5 flex-wrap">
        <div className="relative flex-1 min-w-0 w-full sm:min-w-[200px]">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            ref={searchRef}
            className="input pl-8"
            placeholder="Search… (origin_chapter:N-M)"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <select className="input w-full sm:w-52" value={filterBook} onChange={e => setFilterBook(e.target.value)}>
          <option value="">All Books</option>
          <option value="global">Global Entities</option>
          {books.map((b, i) => (
            <option key={b.id} value={b.id}>{i + 1}. {b.title}</option>
          ))}
        </select>
        <select className="input w-full sm:w-44" value={filterCat} onChange={e => setFilterCat(e.target.value)}>
          <option value="">All categories</option>
          {activeCategories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {error && <p className="text-rose-400 text-sm mb-4">{error}</p>}

      {/* Summary bar */}
      {!loading && entities.length > 0 && (
        <div className="flex items-center gap-3 mb-4 text-xs text-slate-500 flex-wrap">
          <span>{entities.length} entities</span>
          <span className="text-slate-700">|</span>
          {Object.entries(grouped).map(([cat, ents]) => (
            <span key={cat} className="flex items-center gap-1">
              <span {...catBadgeProps(cat, '!text-[10px] !px-1.5 !py-0')}>{cat}</span>
              {ents.length}
            </span>
          ))}
          {notedCount > 0 && (
            <>
              <span className="text-slate-700">|</span>
              <span className="text-amber-500/70">{notedCount} noted</span>
            </>
          )}
        </div>
      )}

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 mb-4 px-4 py-2.5 rounded-lg bg-indigo-950/40 border border-indigo-800/50 flex-wrap">
          <span className="text-xs text-indigo-300 font-medium">{selected.size} selected</span>
          <div className="flex gap-2 flex-wrap">
            <button className="btn-secondary flex items-center gap-1.5 text-xs !py-1" onClick={() => batchModal.open('move_category')}>
              <ArrowRightLeft size={12} /> Move Category
            </button>
            <button className="btn-secondary flex items-center gap-1.5 text-xs !py-1" onClick={() => batchModal.open('change_book')}>
              <FolderInput size={12} /> Change Book
            </button>
            <button className="btn-secondary flex items-center gap-1.5 text-xs !py-1 hover:!text-rose-400 hover:!border-rose-800" onClick={handleBatchDelete}>
              <Trash2 size={12} /> Delete
            </button>
          </div>
          <button className="ml-auto text-xs text-slate-500 hover:text-slate-300" onClick={clearSelection}>Clear</button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-slate-400 text-sm">
          <Loader2 size={14} className="animate-spin" /> Loading…
        </div>
      ) : entities.length === 0 ? (
        <div className="card p-8 text-center text-slate-500">No entities found.</div>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped).map(([cat, catEntities]) => (
            <CategorySection
              key={cat}
              category={cat}
              entities={catEntities}
              onEdit={(ent) => editEntityModal.open(ent.id)}
              onDelete={handleDelete}
              defaultOpen={!!debouncedSearch || !!filterCat}
              selected={selected}
              onToggleSelect={toggleSelect}
              onSetSelected={setSelected}
            />
          ))}
        </div>
      )}

      {/* Modals */}
      {(addModal.isOpen || editingEntity) && (
        <EntityFormModal
          entity={editingEntity || prefillEntity}
          books={books}
          categories={activeCategories}
          onClose={() => { addModal.close(); editEntityModal.close() }}
          onSaved={() => { addModal.close(); editEntityModal.close(); load() }}
        />
      )}

      {duplicatesModal.isOpen && duplicates && (
        <DuplicatesModal
          duplicates={duplicates}
          books={books}
          onClose={duplicatesModal.close}
          onResolved={load}
        />
      )}

      {batchModal.isOpen && batchModal.id === 'move_category' && (
        <BatchCategoryModal
          count={selected.size}
          categories={activeCategories}
          onClose={batchModal.close}
          onConfirm={(category) => handleBatchAction('move_category', { category })}
        />
      )}

      {batchModal.isOpen && batchModal.id === 'change_book' && (
        <BatchBookModal
          count={selected.size}
          books={books}
          onClose={batchModal.close}
          onConfirm={(book_id) => handleBatchAction('change_book', { book_id })}
        />
      )}

      {pendingDelete && (
        <DeleteEntityModal
          entities={pendingDelete.entities}
          onConfirm={confirmDelete}
          onCancel={closeDeleteModal}
        />
      )}
    </div>
  )
}

const BASE_SORT_COLS = [
  { key: 'untranslated', label: 'Untranslated' },
  { key: 'translation',  label: 'Translation' },
  { key: 'origin_chapter', label: 'Origin Ch.' },
  { key: 'last_chapter', label: 'Last Ch.' },
]

const GENDER_COL = { key: 'gender', label: 'Gender' }

function SortIcon({ col, sortCol, sortDir }) {
  if (sortCol !== col) return <ChevronsUpDown size={11} className="text-slate-600 ml-1 inline-block" />
  return sortDir === 'asc'
    ? <ChevronUp size={11} className="text-indigo-400 ml-1 inline-block" />
    : <ChevronDown size={11} className="text-indigo-400 ml-1 inline-block" />
}

function CategorySection({ category, entities, onEdit, onDelete, defaultOpen, selected, onToggleSelect, onSetSelected }) {
  const [open, setOpen] = useState(defaultOpen)
  const [showAll, setShowAll] = useState(false)
  const [sortCol, setSortCol] = useState(null)
  const [sortDir, setSortDir] = useState('asc')
  const showGender = category === 'characters'
  const cols = showGender
    ? [BASE_SORT_COLS[0], BASE_SORT_COLS[1], GENDER_COL, BASE_SORT_COLS[2]]
    : BASE_SORT_COLS

  const catSelectedCount = entities.filter(e => selected.has(e.id)).length
  const allCatSelected = catSelectedCount === entities.length && entities.length > 0
  const toggleCategorySelect = () => {
    const catIds = entities.map(e => e.id)
    onSetSelected(prev => {
      const next = new Set(prev)
      if (allCatSelected) {
        catIds.forEach(id => next.delete(id))
      } else {
        catIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  // Reset open state when defaultOpen changes (e.g. search activates)
  useEffect(() => { setOpen(defaultOpen) }, [defaultOpen])

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortCol(col)
      setSortDir('asc')
    }
  }

  // Sort with noted entities pinned to top
  const sorted = useMemo(() => {
    const arr = [...entities]
    arr.sort((a, b) => {
      // Noted entities always first
      const aNoted = a.note ? 1 : 0
      const bNoted = b.note ? 1 : 0
      if (aNoted !== bNoted) return bNoted - aNoted

      if (!sortCol) return 0
      const av = a[sortCol] ?? ''
      const bv = b[sortCol] ?? ''
      if (sortCol === 'last_chapter' || sortCol === 'origin_chapter') {
        const an = Number(av) || 0
        const bn = Number(bv) || 0
        return sortDir === 'asc' ? an - bn : bn - an
      }
      const cmp = String(av).localeCompare(String(bv), undefined, { sensitivity: 'base' })
      return sortDir === 'asc' ? cmp : -cmp
    })
    return arr
  }, [entities, sortCol, sortDir])

  const isTruncated = !showAll && sorted.length > TRUNCATE_LIMIT
  const visible = isTruncated ? sorted.slice(0, TRUNCATE_LIMIT) : sorted
  const notedCount = entities.filter(e => e.note).length

  return (
    <div className="card overflow-hidden overflow-x-auto">
      <button
        className="w-full flex items-center justify-between px-4 py-3 border-b border-slate-700 hover:bg-slate-750"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex items-center gap-2">
          <span {...catBadgeProps(category)}>{category}</span>
          <span className="text-xs text-slate-500">{entities.length} entries</span>
          {notedCount > 0 && (
            <span className="text-xs text-amber-500/60">{notedCount} noted</span>
          )}
        </div>
        <ChevronDown size={14} className={`text-slate-500 transition-transform ${open ? '' : '-rotate-90'}`} />
      </button>

      {open && (
        <>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 border-b border-slate-700">
                <th className="pl-4 pr-1 py-2 w-8">
                  <button className="flex items-center hover:text-slate-300" onClick={toggleCategorySelect}>
                    {allCatSelected
                      ? <CheckSquare size={14} className="text-indigo-400" />
                      : catSelectedCount > 0
                        ? <CheckSquare size={14} className="text-indigo-400/40" />
                        : <Square size={14} />}
                  </button>
                </th>
                {cols.map(({ key, label }) => (
                  <th key={key} className="text-left px-4 py-2 font-medium">
                    <button
                      className="flex items-center hover:text-slate-300 transition-colors"
                      onClick={() => handleSort(key)}
                    >
                      {label}
                      <SortIcon col={key} sortCol={sortCol} sortDir={sortDir} />
                    </button>
                  </th>
                ))}
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {visible.map(e => (
                <tr key={e.id} className={`border-b border-slate-800 last:border-0 hover:bg-slate-750/50${selected.has(e.id) ? ' !bg-indigo-950/30' : ''}${e.note ? ' bg-amber-950/10' : ''}`}>
                  <td className="pl-4 pr-1 py-2 w-8">
                    <button className="flex items-center" onClick={() => onToggleSelect(e.id)}>
                      {selected.has(e.id)
                        ? <CheckSquare size={14} className="text-indigo-400" />
                        : <Square size={14} className="text-slate-600" />}
                    </button>
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-300">
                    {e.note && <Pin size={10} className="inline-block text-amber-500/50 mr-1.5 -mt-0.5" />}
                    {e.untranslated}
                  </td>
                  <td className="px-4 py-2 text-slate-200">
                    <span>{e.translation}</span>
                    {e.note && <span className="ml-2 text-xs text-amber-500/50 italic">{e.note}</span>}
                  </td>
                  {showGender && <td className="px-4 py-2 text-xs text-slate-500">{e.gender || '—'}</td>}
                  <td className="px-4 py-2 text-xs text-slate-500">{e.origin_chapter || '—'}</td>
                  <td className="px-4 py-2 text-xs text-slate-500">{e.last_chapter || '—'}</td>
                  <td className="px-4 py-2">
                    <div className="flex gap-1 justify-end">
                      <button className="btn-ghost p-1" onClick={() => onEdit(e)}>
                        <Edit2 size={12} />
                      </button>
                      <button className="btn-ghost p-1 hover:text-rose-400" onClick={() => onDelete(e.id)}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {isTruncated && (
            <button
              className="w-full py-2 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-750/50 transition-colors"
              onClick={() => setShowAll(true)}
            >
              Show all {sorted.length} ({sorted.length - TRUNCATE_LIMIT} more)
            </button>
          )}
        </>
      )}
    </div>
  )
}

function BatchCategoryModal({ count, categories, onClose, onConfirm }) {
  const [category, setCategory] = useState(categories[0] || '')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-sm p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">Move {count} entities to category</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>
        <div>
          <label className="label">Target category</label>
          <select className="input" value={category} onChange={e => setCategory(e.target.value)}>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex items-center gap-1.5" onClick={() => onConfirm(category)}>
            <ArrowRightLeft size={13} /> Move
          </button>
        </div>
      </div>
    </div>
  )
}

function BatchBookModal({ count, books, onClose, onConfirm }) {
  const [bookId, setBookId] = useState('')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-sm p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-200">Move {count} entities to book</h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>
        <div>
          <label className="label">Target book</label>
          <select className="input" value={bookId} onChange={e => setBookId(e.target.value)}>
            <option value="">Global (no book)</option>
            {books.map(b => <option key={b.id} value={b.id}>{b.id}: {b.title}</option>)}
          </select>
        </div>
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex items-center gap-1.5" onClick={() => onConfirm(bookId ? parseInt(bookId) : null)}>
            <FolderInput size={13} /> Move
          </button>
        </div>
      </div>
    </div>
  )
}

function DuplicatesModal({ duplicates, books, onClose, onResolved }) {
  const [dupUntranslated, setDupUntranslated] = useState(duplicates.duplicate_untranslated || [])
  const [dupTranslations, setDupTranslations] = useState(duplicates.duplicate_translations || [])
  const scrollRef = useRef(null)
  const nextItemRef = useRef(null)

  const total = dupUntranslated.length + dupTranslations.length

  const bookName = (bookId) => {
    if (bookId == null) return 'Global Entities'
    const b = books.find(b => b.id === bookId)
    return b ? `Book ${b.id}: ${b.title}` : `Book ${bookId}`
  }

  const groupByBook = (items) => {
    const groups = {}
    for (const item of items) {
      const key = item.book_id ?? 'global'
      if (!groups[key]) groups[key] = { book_id: item.book_id, items: [] }
      groups[key].items.push(item)
    }
    return Object.values(groups)
  }

  const handleUntranslatedResolved = (untranslated, bookId) => {
    // Find the index of the resolved item so we can scroll to the next one
    const idx = dupUntranslated.findIndex(d => d.untranslated === untranslated && d.book_id === bookId)
    setDupUntranslated(prev => prev.filter(d => !(d.untranslated === untranslated && d.book_id === bookId)))
    onResolved()
    // After state update, scroll to the next item
    setTimeout(() => nextItemRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50)
  }

  const untranslatedByBook = groupByBook(dupUntranslated)
  const translationsByBook = groupByBook(dupTranslations)

  // Flatten untranslated items to assign nextItemRef to the one after a resolved item
  const allUntranslatedItems = untranslatedByBook.flatMap(g => g.items)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700">
          <h2 className="font-semibold text-slate-200">
            Duplicates {total === 0 ? '— None found' : `(${total} groups)`}
          </h2>
          <button className="btn-ghost p-1" onClick={onClose}><X size={16} /></button>
        </div>

        <div ref={scrollRef} className="overflow-y-auto flex-1 p-5 space-y-6">
          {total === 0 && (
            <p className="text-emerald-400 text-sm">No duplicates found. Database is clean.</p>
          )}

          {dupUntranslated.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-slate-300 mb-3">Same Chinese, different categories</h3>
              <div className="space-y-4">
                {untranslatedByBook.map(group => (
                  <div key={group.book_id ?? 'global'}>
                    <p className="text-xs font-medium text-indigo-400 mb-2">{bookName(group.book_id)}</p>
                    <div className="space-y-3">
                      {group.items.map((dup, i) => (
                        <DupUntranslatedItem
                          key={dup.untranslated}
                          ref={i === 0 ? nextItemRef : undefined}
                          dup={dup}
                          onResolved={() => handleUntranslatedResolved(dup.untranslated, dup.book_id)}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {dupTranslations.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-slate-300 mb-3">Same English, different Chinese</h3>
              <div className="space-y-4">
                {translationsByBook.map(group => (
                  <div key={group.book_id ?? 'global'}>
                    <p className="text-xs font-medium text-indigo-400 mb-2">{bookName(group.book_id)}</p>
                    <div className="space-y-2">
                      {group.items.map(dup => (
                        <div key={dup.translation} className="card p-3">
                          <p className="text-sm font-medium text-slate-200 mb-2">"{dup.translation}"</p>
                          <div className="space-y-1">
                            {dup.instances.map(inst => (
                              <div key={inst.id} className="flex items-center gap-2 text-xs text-slate-400">
                                <span {...catBadgeProps(inst.category)}>{inst.category}</span>
                                <span className="font-mono">{inst.untranslated}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

const DupUntranslatedItem = React.forwardRef(function DupUntranslatedItem({ dup, onResolved }, ref) {
  const [resolving, setResolving] = useState(false)
  const [resolved, setResolved] = useState(false)
  const [showDeleteModal, setShowDeleteModal] = useState(false)

  const handleKeep = async (category) => {
    setResolving(true)
    try {
      await api.resolveDuplicate({ untranslated: dup.untranslated, action: 'keep_one', keep_category: category, book_id: dup.book_id ?? null })
      setResolved(true)
      setTimeout(() => onResolved(), 300)
    } catch (e) {
      alert(e.message)
      setResolving(false)
    }
  }

  const handleDeleteAll = async (decase) => {
    setShowDeleteModal(false)
    setResolving(true)
    try {
      if (decase) {
        const seen = new Set()
        for (const inst of dup.instances) {
          if (!inst.book_id || !inst.translation || !/^[A-Z]/.test(inst.translation)) continue
          const key = `${inst.book_id}::${inst.translation}`
          if (seen.has(key)) continue
          seen.add(key)
          await api.decaseEntity({ translation: inst.translation, book_id: inst.book_id })
        }
      }
      await api.resolveDuplicate({ untranslated: dup.untranslated, action: 'delete_all', book_id: dup.book_id ?? null })
      setResolved(true)
      setTimeout(() => onResolved(), 300)
    } catch (e) {
      alert(e.message)
      setResolving(false)
    }
  }

  if (resolved) {
    return (
      <div ref={ref} className="card p-3 opacity-40 transition-opacity duration-300">
        <p className="text-sm text-emerald-400">Resolved: {dup.untranslated}</p>
      </div>
    )
  }

  return (
    <div ref={ref} className="card p-3">
      <div className="flex items-center justify-between mb-2">
        <p className="text-sm font-mono text-slate-200">{dup.untranslated}</p>
        <button
          className="text-xs btn-ghost hover:text-rose-400 flex items-center gap-1"
          onClick={() => setShowDeleteModal(true)}
          disabled={resolving}
          title="Delete all instances of this entity"
        >
          <Trash2 size={11} /> {dup.instances.length <= 2 ? 'Delete both' : 'Delete all'}
        </button>
      </div>
      <div className="space-y-1.5">
        {dup.instances.map(inst => (
          <div key={inst.id} className="flex items-center gap-2">
            <span {...catBadgeProps(inst.category)}>{inst.category}</span>
            <span className="text-sm text-slate-300 flex-1">{inst.translation}</span>
            <button
              className="text-xs btn-secondary"
              onClick={() => handleKeep(inst.category)}
              disabled={resolving}
            >
              Keep this
            </button>
          </div>
        ))}
      </div>
      {showDeleteModal && (
        <DeleteEntityModal
          entities={dup.instances}
          onConfirm={handleDeleteAll}
          onCancel={() => setShowDeleteModal(false)}
        />
      )}
    </div>
  )
})
