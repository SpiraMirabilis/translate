/**
 * BookPicker — a type-to-filter replacement for the book <select>s.
 *
 * Behaves like a select (the value is always one of the options, never free
 * text — that's what ComboBox.jsx is for): focus it and start typing a title or
 * a book id, arrow through the matches, Enter to pick, Esc/blur to back out.
 * onChange receives the option's value as a string, exactly as a <select>'s
 * e.target.value would, so call sites swap over without touching their state.
 *
 * `extraOptions` ([{value, label}]) sit above the books — "All books",
 * "Global", "Create book…". The list is portaled to <body> with fixed
 * positioning, so it isn't clipped by a modal's overflow.
 */
import { useState, useRef, useEffect, useLayoutEffect, useId, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from 'lucide-react'
import { rankBooks, bookLabel } from '../lib/bookMatch'

export default function BookPicker({
  books = [],
  value,
  onChange,
  extraOptions = [],
  placeholder = 'Select book…',
  className = '',
  inputClassName = 'input',
  disabled = false,
  title,
}) {
  // null = not editing: the input shows the selected option's label and the
  // list shows everything. A string = what the user has typed.
  const [query, setQuery] = useState(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [pos, setPos] = useState(null)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const listId = useId()

  const current = value == null ? '' : String(value)
  const selectedLabel = useMemo(() => {
    const extra = extraOptions.find(o => String(o.value) === current)
    if (extra) return extra.label
    const book = books.find(b => String(b.id) === current)
    if (book) return bookLabel(book)
    return current ? `Book ${current}` : ''
  }, [books, extraOptions, current])

  const options = useMemo(() => {
    const q = (query || '').trim().toLowerCase()
    const extras = extraOptions
      .filter(o => !q || o.label.toLowerCase().includes(q))
      .map(o => ({ value: String(o.value), label: o.label }))
    const matched = rankBooks(books, query || '')
      .map(b => ({ value: String(b.id), label: bookLabel(b) }))
    return [...extras, ...matched]
  }, [books, extraOptions, query])

  // Opening with no query highlights the current selection; typing resets to
  // the best match.
  useEffect(() => {
    if (!open) return
    if (query == null) {
      const idx = options.findIndex(o => o.value === current)
      setActive(idx >= 0 ? idx : 0)
    } else {
      setActive(0)
    }
  }, [open, query]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the list anchored to the input while anything scrolls or resizes.
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const r = inputRef.current?.getBoundingClientRect()
      if (!r) return
      const below = window.innerHeight - r.bottom
      const up = below < 220 && r.top > below
      setPos({
        left: r.left,
        minWidth: r.width,
        maxWidth: Math.max(r.width, Math.min(512, window.innerWidth - r.left - 8)),
        ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }),
        maxHeight: Math.min(288, (up ? r.top : below) - 12),
      })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    listRef.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active])

  const close = () => { setOpen(false); setQuery(null) }

  const pick = (opt) => {
    if (opt.value !== current) onChange(opt.value)
    close()
    inputRef.current?.blur()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      const step = e.key === 'ArrowDown' ? 1 : -1
      setActive(i => Math.min(Math.max(i + step, 0), Math.max(options.length - 1, 0)))
    } else if (e.key === 'Enter') {
      // Never let Enter submit a surrounding form while choosing.
      if (open) e.preventDefault()
      if (open && options[active]) pick(options[active])
    } else if (e.key === 'Escape') {
      if (open) { e.preventDefault(); e.stopPropagation() }
      close()
      inputRef.current?.blur()
    } else if (e.key === 'Home' || e.key === 'End') {
      if (open && query == null) {
        e.preventDefault()
        setActive(e.key === 'Home' ? 0 : options.length - 1)
      }
    }
  }

  const list = open && pos && createPortal(
    <ul
      ref={listRef}
      id={listId}
      role="listbox"
      style={{ position: 'fixed', ...pos }}
      className="z-[1000] overflow-y-auto bg-slate-800 border border-slate-600 rounded shadow-xl py-1"
      // Keep focus in the input: clicking an option or the scrollbar must not blur it.
      onMouseDown={e => e.preventDefault()}
    >
      {options.length === 0 ? (
        <li className="px-3 py-1.5 text-sm text-slate-500">No matching books</li>
      ) : options.map((opt, i) => (
        <li
          key={opt.value}
          id={`${listId}-${i}`}
          data-idx={i}
          role="option"
          aria-selected={opt.value === current}
          className={`px-3 py-1.5 text-sm cursor-pointer truncate
                      ${i === active ? 'bg-indigo-600 text-white' : opt.value === current ? 'text-indigo-300' : 'text-slate-200'}`}
          title={opt.label}
          onMouseEnter={() => setActive(i)}
          onClick={() => pick(opt)}
        >
          {highlight(opt.label, query)}
        </li>
      ))}
    </ul>,
    document.body,
  )

  return (
    <div className={`relative ${className}`}>
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
        className={`${inputClassName} pr-7 truncate`}
        value={query ?? selectedLabel}
        placeholder={placeholder}
        title={title ?? selectedLabel}
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
        onFocus={e => { setOpen(true); e.target.select() }}
        onClick={() => setOpen(true)}
        onBlur={close}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onKeyDown={handleKeyDown}
      />
      <ChevronDown
        size={14}
        className={`absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none transition-transform ${open ? 'rotate-180' : ''}`}
      />
      {list}
    </div>
  )
}

/** Bold the typed text where it appears contiguously in the label. */
function highlight(text, query) {
  const q = (query || '').trim()
  if (!q) return text
  const idx = text.toLowerCase().indexOf(q.toLowerCase())
  if (idx === -1) return text
  return (
    <>
      {text.slice(0, idx)}
      <span className="font-bold text-white">{text.slice(idx, idx + q.length)}</span>
      {text.slice(idx + q.length)}
    </>
  )
}
