/**
 * ComboBox — free-text input with a filtered suggestion dropdown.
 * Type anything, or pick from the list. Click away to close.
 */
import { useState, useRef, useEffect } from 'react'
import { ChevronDown, X } from 'lucide-react'

export default function ComboBox({ value, onChange, options = [], placeholder = '' }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState(value || '')
  const containerRef = useRef(null)
  const inputRef = useRef(null)

  // Keep query in sync when value is reset externally (e.g. clear button)
  useEffect(() => {
    setQuery(value || '')
  }, [value])

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
        // Commit whatever is typed as the value
        onChange(query.trim())
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [query, onChange])

  const filtered = query
    ? options.filter(o => o.toLowerCase().includes(query.toLowerCase()))
    : options

  const handleSelect = (opt) => {
    setQuery(opt)
    onChange(opt)
    setOpen(false)
  }

  const handleInputChange = (e) => {
    setQuery(e.target.value)
    onChange(e.target.value)
    setOpen(true)
  }

  const handleClear = () => {
    setQuery('')
    onChange('')
    inputRef.current?.focus()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') { setOpen(false); inputRef.current?.blur() }
    if (e.key === 'ArrowDown' && filtered.length > 0) {
      e.preventDefault()
      setOpen(true)
      // Focus first item
      containerRef.current?.querySelector('[data-option]')?.focus()
    }
  }

  const handleOptionKeyDown = (e, opt, index) => {
    if (e.key === 'Enter' || e.key === ' ') { handleSelect(opt) }
    if (e.key === 'Escape') { setOpen(false); inputRef.current?.focus() }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      const items = containerRef.current?.querySelectorAll('[data-option]')
      items?.[index + 1]?.focus()
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (index === 0) { inputRef.current?.focus() }
      else {
        const items = containerRef.current?.querySelectorAll('[data-option]')
        items?.[index - 1]?.focus()
      }
    }
  }

  return (
    <div ref={containerRef} className="relative">
      {/* Input */}
      <div className="relative flex items-center">
        <input
          ref={inputRef}
          type="text"
          className="input text-sm pr-14 font-mono"
          value={query}
          placeholder={placeholder}
          onChange={handleInputChange}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          autoComplete="off"
          spellCheck={false}
        />
        <div className="absolute right-0 flex items-center pr-1 gap-0.5">
          {query && (
            <button
              className="p-1 text-slate-500 hover:text-slate-300"
              onClick={handleClear}
              tabIndex={-1}
              title="Clear"
            >
              <X size={12} />
            </button>
          )}
          <button
            className="p-1 text-slate-500 hover:text-slate-300"
            onClick={() => { setOpen(v => !v); inputRef.current?.focus() }}
            tabIndex={-1}
          >
            <ChevronDown size={13} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {/* Dropdown */}
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full max-h-56 overflow-y-auto
                       bg-slate-800 border border-slate-600 rounded shadow-xl">
          {filtered.map((opt, i) => (
            <li
              key={opt}
              data-option
              tabIndex={0}
              className={`px-3 py-1.5 text-sm font-mono cursor-pointer outline-none
                          hover:bg-indigo-600 focus:bg-indigo-600
                          ${opt === value ? 'text-indigo-300' : 'text-slate-200'}`}
              onMouseDown={() => handleSelect(opt)}
              onKeyDown={(e) => handleOptionKeyDown(e, opt, i)}
            >
              {highlight(opt, query)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Bold the matching portion of each option. */
function highlight(text, query) {
  if (!query) return text
  const idx = text.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return text
  return (
    <>
      {text.slice(0, idx)}
      <span className="font-bold text-white">{text.slice(idx, idx + query.length)}</span>
      {text.slice(idx + query.length)}
    </>
  )
}
