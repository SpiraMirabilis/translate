import { Search, Replace, ChevronLeft, ChevronRight, X, BookOpen } from 'lucide-react'

export default function SearchBar({
  search,
  onNext,
  onPrev,
  onReplace,
  onReplaceAll,
  onClose,
}) {
  if (!search.isOpen) return null

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onNext()
    } else if (e.key === 'Enter' && e.shiftKey) {
      e.preventDefault()
      onPrev()
    }
  }

  const canReplace = search.scope !== 'untranslated'

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border-b border-slate-800 text-xs flex-shrink-0">
      {/* Search icon */}
      <Search size={13} className="text-slate-500 shrink-0" />

      {/* Search input */}
      <input
        ref={search.searchInputRef}
        type="text"
        value={search.query}
        onChange={(e) => search.setQuery(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Search..."
        className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200
                   placeholder-slate-600 focus:outline-none focus:border-indigo-500/50
                   w-40 sm:w-48"
      />

      {/* Replace input */}
      {canReplace && (
        <>
          <Replace size={13} className="text-slate-500 shrink-0" />
          <input
            ref={search.replaceInputRef}
            type="text"
            value={search.replaceText}
            onChange={(e) => search.setReplaceText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Replace..."
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200
                       placeholder-slate-600 focus:outline-none focus:border-indigo-500/50
                       w-32 sm:w-40"
          />
        </>
      )}

      {/* Scope dropdown */}
      <select
        value={search.scope}
        onChange={(e) => search.setScope(e.target.value)}
        className="bg-slate-800 border border-slate-700 rounded px-1.5 py-1 text-slate-300
                   focus:outline-none focus:border-indigo-500/50 cursor-pointer"
      >
        <option value="translated">Translated</option>
        <option value="untranslated">Source</option>
        <option value="both">Both</option>
      </select>

      {/* Regex toggle */}
      <button
        className={`px-1.5 py-1 rounded border font-mono transition-colors ${
          search.isRegex
            ? 'border-indigo-500/50 bg-indigo-500/20 text-indigo-300'
            : 'border-slate-700 text-slate-500 hover:text-slate-400'
        }`}
        onClick={() => search.setIsRegex(!search.isRegex)}
        title="Toggle regex mode"
      >
        .*
      </button>

      {/* Book-wide toggle */}
      <button
        className={`px-1.5 py-1 rounded border flex items-center gap-1 transition-colors ${
          search.isBookWide
            ? 'border-indigo-500/50 bg-indigo-500/20 text-indigo-300'
            : 'border-slate-700 text-slate-500 hover:text-slate-400'
        }`}
        onClick={() => search.setIsBookWide(!search.isBookWide)}
        title="Toggle book-wide search"
      >
        <BookOpen size={12} />
        Book
      </button>

      {/* Separator */}
      <div className="w-px h-4 bg-slate-700" />

      {/* Navigation */}
      <button
        className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200
                   disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        onClick={onPrev}
        disabled={search.totalMatches === 0}
        title="Previous match (Shift+Enter)"
      >
        <ChevronLeft size={14} />
      </button>

      <span className="text-slate-400 min-w-[4rem] text-center select-none tabular-nums">
        {search.bookSearchLoading
          ? '...'
          : search.totalMatches > 0
            ? `${search.currentIndex + 1} of ${search.totalMatches}`
            : search.query
              ? 'No results'
              : ''
        }
      </span>

      <button
        className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200
                   disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        onClick={onNext}
        disabled={search.totalMatches === 0}
        title="Next match (Enter)"
      >
        <ChevronRight size={14} />
      </button>

      {/* Replace buttons */}
      {canReplace && search.query && (
        <>
          <div className="w-px h-4 bg-slate-700" />
          <button
            className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200
                       hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            onClick={onReplace}
            disabled={search.totalMatches === 0}
            title="Replace current match"
          >
            Replace
          </button>
          <button
            className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200
                       hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            onClick={onReplaceAll}
            disabled={search.totalMatches === 0}
            title="Replace all matches"
          >
            All
          </button>
        </>
      )}

      {/* Close */}
      <button
        className="p-1 rounded hover:bg-slate-800 text-slate-500 hover:text-slate-300 ml-auto transition-colors"
        onClick={onClose}
        title="Close (Escape)"
      >
        <X size={14} />
      </button>
    </div>
  )
}
