// Per-theme Tailwind tokens for the public Library page and its parts.
// The bg/text pair comes from useReaderPrefs(); everything else lives here so
// BookCard, LibraryToolbar and the shelves share one table.
export const LIBRARY_THEME = {
  light: {
    headerBg: 'bg-white', headerBorder: 'border-stone-200',
    subtitle: 'text-gray-500', cardBg: 'bg-stone-200',
    placeholderIcon: 'text-white/70', placeholderText: 'text-white/90',
    title: 'text-gray-900', titleHover: 'group-hover:text-indigo-600',
    author: 'text-gray-500', chapters: 'text-gray-400',
    toggleBg: 'bg-stone-100', toggleActive: 'bg-white text-indigo-600 shadow-sm',
    toggleInactive: 'text-gray-400 hover:text-gray-600',
    chip: 'bg-stone-100 text-gray-600 hover:bg-stone-200 border-transparent',
    chipActive: 'bg-indigo-600 text-white border-indigo-600 hover:bg-indigo-500',
    chipCount: 'text-gray-400',
    searchBg: 'bg-stone-100 focus-within:bg-white', searchText: 'text-gray-900 placeholder:text-gray-400',
    searchBorder: 'border-stone-200 focus-within:border-indigo-400',
    shelfTitle: 'text-gray-900', shelfMeta: 'text-gray-400',
    progressTrack: 'bg-stone-300', progressFill: 'bg-indigo-500',
    divider: 'border-stone-200', skeleton: 'bg-stone-200',
    // Modal theme tokens
    modalBg: 'bg-white', modalText: 'text-gray-900', modalBorder: 'border-gray-200',
    inputBg: 'bg-gray-50', inputBorder: 'border-gray-300', inputText: 'text-gray-900',
    labelText: 'text-gray-700', subtleText: 'text-gray-500', turnstileTheme: 'light',
    errorButton: 'bg-stone-100 text-gray-700 hover:bg-stone-200 border-stone-200',
  },
  sepia: {
    headerBg: 'bg-amber-100/80', headerBorder: 'border-amber-200',
    subtitle: 'text-amber-800/60', cardBg: 'bg-amber-200/50',
    placeholderIcon: 'text-white/70', placeholderText: 'text-white/90',
    title: 'text-amber-950', titleHover: 'group-hover:text-indigo-700',
    author: 'text-amber-800/60', chapters: 'text-amber-700/50',
    toggleBg: 'bg-amber-200/50', toggleActive: 'bg-amber-50 text-amber-900 shadow-sm',
    toggleInactive: 'text-amber-700/50 hover:text-amber-900',
    chip: 'bg-amber-200/50 text-amber-900/80 hover:bg-amber-200 border-transparent',
    chipActive: 'bg-amber-900 text-amber-50 border-amber-900 hover:bg-amber-800',
    chipCount: 'text-amber-700/50',
    searchBg: 'bg-amber-200/40 focus-within:bg-amber-50', searchText: 'text-amber-950 placeholder:text-amber-700/50',
    searchBorder: 'border-amber-200 focus-within:border-amber-500',
    shelfTitle: 'text-amber-950', shelfMeta: 'text-amber-700/60',
    progressTrack: 'bg-amber-300/60', progressFill: 'bg-amber-700',
    divider: 'border-amber-200', skeleton: 'bg-amber-200/60',
    modalBg: 'bg-amber-50', modalText: 'text-amber-950', modalBorder: 'border-amber-200',
    inputBg: 'bg-amber-100/50', inputBorder: 'border-amber-300', inputText: 'text-amber-950',
    labelText: 'text-amber-800', subtleText: 'text-amber-700/60', turnstileTheme: 'light',
    errorButton: 'bg-amber-100 text-amber-900 hover:bg-amber-200 border-amber-200',
  },
  dark: {
    headerBg: 'bg-slate-800/80', headerBorder: 'border-slate-700',
    subtitle: 'text-slate-400', cardBg: 'bg-slate-800',
    placeholderIcon: 'text-white/60', placeholderText: 'text-white/85',
    title: 'text-slate-100', titleHover: 'group-hover:text-indigo-400',
    author: 'text-slate-400', chapters: 'text-slate-500',
    toggleBg: 'bg-slate-800', toggleActive: 'bg-slate-700 text-slate-100 shadow-sm',
    toggleInactive: 'text-slate-500 hover:text-slate-300',
    chip: 'bg-slate-800 text-slate-300 hover:bg-slate-700 border-transparent',
    chipActive: 'bg-indigo-500 text-white border-indigo-500 hover:bg-indigo-400',
    chipCount: 'text-slate-500',
    searchBg: 'bg-slate-800 focus-within:bg-slate-800', searchText: 'text-slate-100 placeholder:text-slate-500',
    searchBorder: 'border-slate-700 focus-within:border-indigo-400',
    shelfTitle: 'text-slate-100', shelfMeta: 'text-slate-500',
    progressTrack: 'bg-slate-700', progressFill: 'bg-indigo-400',
    divider: 'border-slate-800', skeleton: 'bg-slate-800',
    modalBg: 'bg-slate-800', modalText: 'text-slate-100', modalBorder: 'border-slate-700',
    inputBg: 'bg-slate-700', inputBorder: 'border-slate-600', inputText: 'text-slate-100',
    labelText: 'text-slate-300', subtleText: 'text-slate-400', turnstileTheme: 'dark',
    errorButton: 'bg-slate-800 text-slate-300 hover:bg-slate-700 border-slate-700',
  },
}

// Deterministic cover stand-in for books without artwork: a hue from the id,
// so three coverless books stop looking like one.
export function placeholderStyle(id, themeName) {
  const h = (Number(id) * 47) % 360
  const [l1, l2] = themeName === 'dark' ? [28, 18] : themeName === 'sepia' ? [42, 30] : [48, 36]
  return { background: `linear-gradient(135deg, hsl(${h} 35% ${l1}%), hsl(${(h + 40) % 360} 40% ${l2}%))` }
}

export const STATUS_CHIP = {
  completed: 'bg-emerald-500/20 text-emerald-500',
  hiatus: 'bg-amber-500/20 text-amber-500',
  dropped: 'bg-rose-500/20 text-rose-400',
  'ongoing-trial': 'bg-cyan-500/20 text-cyan-500',
}
