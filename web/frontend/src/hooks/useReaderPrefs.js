import { useLocalStorage } from './useLocalStorage'

const DEFAULTS = {
  fontSize: 18,
  fontFamily: 'serif',
  theme: 'light',
  lineHeight: 1.8,
  margins: 'medium',
}

const MARGIN_CLASS = {
  narrow: 'max-w-4xl',
  medium: 'max-w-2xl',
  wide:   'max-w-lg',
}

const THEME_CLASSES = {
  dark:  { bg: 'bg-slate-900',  text: 'text-slate-200',   accent: 'border-slate-700' },
  light: { bg: 'bg-stone-50',   text: 'text-gray-900',    accent: 'border-stone-200' },
  sepia: { bg: 'bg-amber-50',   text: 'text-amber-950',   accent: 'border-amber-200' },
}

const FONT_MAP = {
  serif: 'Georgia, "Times New Roman", serif',
  sans:  'system-ui, -apple-system, sans-serif',
  mono:  '"JetBrains Mono", "Fira Code", monospace',
}

export function useReaderPrefs() {
  const [prefs, setPrefs] = useLocalStorage('reader-prefs', DEFAULTS)

  const merged = { ...DEFAULTS, ...prefs }
  const theme = THEME_CLASSES[merged.theme] || THEME_CLASSES.light

  const contentStyle = {
    fontSize: `${merged.fontSize}px`,
    lineHeight: merged.lineHeight,
    fontFamily: FONT_MAP[merged.fontFamily] || FONT_MAP.serif,
  }

  const marginClass = MARGIN_CLASS[merged.margins] || MARGIN_CLASS.medium

  return { prefs: merged, setPrefs, theme, contentStyle, marginClass, DEFAULTS }
}
