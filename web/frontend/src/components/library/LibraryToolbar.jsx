import { useEffect, useRef, useState } from 'react'
import { Search, X, SlidersHorizontal, ChevronDown } from 'lucide-react'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { axisChips, languageChips, hasActiveFilters } from '../../lib/libraryFilter'

const STATUS_OPTIONS = [
  { id: '',          label: 'All' },
  { id: 'ongoing',   label: 'Ongoing' },
  { id: 'completed', label: 'Completed' },
  { id: 'hiatus',    label: 'Hiatus' },
]

const PROTAGONIST = [
  { tag: 'female protagonist', label: '♀ Female MC' },
  { tag: 'male protagonist',   label: '♂ Male MC' },
]

// Axes always on show vs. behind "More filters".
const PRIMARY_AXES = ['genre']
const MORE_AXES = ['premise', 'setting', 'romance', 'tone', 'theme']
const AXIS_LABEL = { premise: 'Premise', setting: 'Setting', romance: 'Romance', tone: 'Tone', theme: 'Theme' }

function Chip({ active, onClick, t, children, count }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex shrink-0 items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors whitespace-nowrap ${active ? t.chipActive : t.chip}`}
    >
      {children}
      {count != null && !active && <span className={`${t.chipCount} tabular-nums`}>{count}</span>}
    </button>
  )
}

// Search + sort + filter chips. Filters are owned by the page (URL state);
// this component only renders them and reports changes. The search box is
// debounced locally so typing never thrashes the URL.
export default function LibraryToolbar({ books, filters, onChange, sort, setSort, sortOptions, resultCount, t }) {
  const [q, setQ] = useState(filters.q || '')
  const debouncedQ = useDebouncedValue(q, 200)
  const inputRef = useRef(null)
  const [more, setMore] = useState(false)

  // Local → URL, and URL → local (back button, "Clear all").
  useEffect(() => { if (debouncedQ !== (filters.q || '')) onChange({ q: debouncedQ }) }, [debouncedQ]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if ((filters.q || '') !== q) setQ(filters.q || '') }, [filters.q]) // eslint-disable-line react-hooks/exhaustive-deps

  // "/" focuses the search box from anywhere on the page.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return
      const tag = document.activeElement?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      e.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const selected = new Set((filters.tags || []).map(s => s.toLowerCase()))
  const toggleTag = (tag) => {
    const next = new Set(selected)
    next.has(tag) ? next.delete(tag) : next.add(tag)
    onChange({ tags: [...next] })
  }
  const active = hasActiveFilters(filters)
  const langs = languageChips(books)
  const moreCount = MORE_AXES.reduce((n, a) => n + axisChips(books, a).filter(c => selected.has(c.tag)).length, 0)
    + (filters.lang ? 1 : 0)

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <label className={`flex-1 flex items-center gap-2 px-3 h-9 rounded-lg border ${t.searchBg} ${t.searchBorder} transition-colors`}>
          <Search size={15} className="opacity-50 shrink-0" />
          <input
            ref={inputRef}
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search title, original title, author, tag…"
            aria-label="Search books"
            className={`flex-1 min-w-0 bg-transparent text-sm outline-none ${t.searchText}`}
          />
          {q ? (
            <button type="button" onClick={() => setQ('')} aria-label="Clear search" className="opacity-50 hover:opacity-100"><X size={14} /></button>
          ) : (
            <kbd className={`hidden sm:inline text-[10px] px-1.5 py-0.5 rounded border ${t.divider} opacity-50`}>/</kbd>
          )}
        </label>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          aria-label="Sort books"
          className={`h-9 max-w-[9rem] text-sm rounded-lg px-2 border-none focus:outline-none focus:ring-2 focus:ring-indigo-400 ${t.toggleBg} ${t.toggleInactive}`}
        >
          {sortOptions.map(opt => <option key={opt.id} value={opt.id}>{opt.label}</option>)}
        </select>
      </div>

      <div className="flex items-center gap-1.5 flex-nowrap overflow-x-auto sm:flex-wrap sm:overflow-visible -mx-4 px-4 sm:mx-0 sm:px-0 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <div className={`inline-flex shrink-0 rounded-full p-0.5 ${t.toggleBg}`} role="group" aria-label="Status">
          {STATUS_OPTIONS.map(s => (
            <button key={s.id} type="button" onClick={() => onChange({ status: s.id })}
              className={`px-2.5 py-0.5 rounded-full text-xs font-medium transition-colors ${filters.status === s.id ? t.toggleActive : t.toggleInactive}`}>
              {s.label}
            </button>
          ))}
        </div>
        <span className={`w-px h-5 mx-0.5 shrink-0 ${t.toggleBg}`} />
        {PROTAGONIST.map(p => (
          <Chip key={p.tag} t={t} active={selected.has(p.tag)} onClick={() => toggleTag(p.tag)}>{p.label}</Chip>
        ))}
        <span className={`w-px h-5 mx-0.5 shrink-0 ${t.toggleBg}`} />
        {PRIMARY_AXES.flatMap(a => axisChips(books, a)).map(c => (
          <Chip key={c.tag} t={t} count={c.count} active={selected.has(c.tag)} onClick={() => toggleTag(c.tag)}>{c.tag}</Chip>
        ))}
        <button type="button" onClick={() => setMore(m => !m)} aria-expanded={more}
          className={`inline-flex shrink-0 items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors whitespace-nowrap ${moreCount ? t.chipActive : t.chip}`}>
          <SlidersHorizontal size={12} /> More{moreCount ? ` · ${moreCount}` : ''}
          <ChevronDown size={12} className={`transition-transform ${more ? 'rotate-180' : ''}`} />
        </button>
        {active && (
          <button type="button" onClick={() => onChange({ q: '', tags: [], status: '', lang: '' })}
            className={`ml-auto shrink-0 inline-flex items-center gap-1 text-xs whitespace-nowrap ${t.toggleInactive}`}>
            <X size={12} /> Clear all{resultCount != null && <span className="opacity-70">· {resultCount} book{resultCount === 1 ? '' : 's'}</span>}
          </button>
        )}
      </div>

      {more && (
        <div className={`grid gap-2 pt-2 border-t ${t.divider}`}>
          {MORE_AXES.map(axis => {
            const chips = axisChips(books, axis)
            if (!chips.length) return null
            return (
              <div key={axis} className="flex items-baseline gap-2 flex-wrap">
                <span className={`text-[11px] uppercase tracking-wide w-16 shrink-0 ${t.chipCount}`}>{AXIS_LABEL[axis]}</span>
                {chips.map(c => (
                  <Chip key={c.tag} t={t} count={c.count} active={selected.has(c.tag)} onClick={() => toggleTag(c.tag)}>{c.tag}</Chip>
                ))}
              </div>
            )
          })}
          {langs.length > 1 && (
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className={`text-[11px] uppercase tracking-wide w-16 shrink-0 ${t.chipCount}`}>Language</span>
              {langs.map(c => (
                <Chip key={c.tag} t={t} count={c.count} active={filters.lang === c.tag}
                  onClick={() => onChange({ lang: filters.lang === c.tag ? '' : c.tag })}>{c.tag}</Chip>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
