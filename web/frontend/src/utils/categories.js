export const DEFAULT_CATEGORIES = [
  'characters', 'places', 'organizations', 'abilities',
  'titles', 'equipment', 'creatures'
]

// rgba-based colors for entity highlighting overlays (used in ChapterEditor, Reader)
export const CATEGORY_COLORS = {
  characters:    { bg: 'rgba(99,102,241,0.28)',  border: 'rgba(99,102,241,0.6)' },   // indigo
  places:        { bg: 'rgba(34,197,94,0.28)',   border: 'rgba(34,197,94,0.6)' },    // green
  organizations: { bg: 'rgba(249,115,22,0.28)',  border: 'rgba(249,115,22,0.6)' },   // orange
  abilities:     { bg: 'rgba(168,85,247,0.28)',  border: 'rgba(168,85,247,0.6)' },   // purple
  titles:        { bg: 'rgba(236,72,153,0.28)',  border: 'rgba(236,72,153,0.6)' },   // pink
  equipment:     { bg: 'rgba(234,179,8,0.28)',   border: 'rgba(234,179,8,0.6)' },    // yellow
  creatures:     { bg: 'rgba(6,182,212,0.28)',   border: 'rgba(6,182,212,0.6)' },    // cyan
}

const DEFAULT_COLORS = {
  characters:    'badge-indigo',
  places:        'badge-emerald',
  organizations: 'badge-amber',
  abilities:     'badge-rose',
  titles:        'badge-slate',
  equipment:     'badge-slate',
  creatures:     'badge-indigo',
}

// HSL hues spread across the spectrum for dynamic categories
const DYNAMIC_HUES = [270, 190, 25, 330, 90, 160, 350, 210, 50, 130, 290, 10]

function hashString(str) {
  let hash = 0
  for (let i = 0; i < str.length; i++)
    hash = ((hash << 5) >>> 0) - hash + str.charCodeAt(i) >>> 0
  return hash
}

/**
 * Returns badge styling for a category.
 * - For default categories: { className: 'badge badge-indigo' }
 * - For dynamic categories: { className: 'badge', style: { backgroundColor, color } }
 */
export function getCatBadge(category) {
  if (DEFAULT_COLORS[category]) {
    return { className: `badge ${DEFAULT_COLORS[category]}` }
  }
  const hue = DYNAMIC_HUES[hashString(category) % DYNAMIC_HUES.length]
  return {
    className: 'badge',
    style: {
      backgroundColor: `hsl(${hue} 50% 20%)`,
      color: `hsl(${hue} 60% 70%)`,
    },
  }
}

/**
 * Spread helper: returns { className, style? } props for a badge element.
 * Usage: <span {...catBadgeProps(cat, 'extra classes')}>{cat}</span>
 */
export function catBadgeProps(category, extraClass = '') {
  const b = getCatBadge(category)
  return {
    className: [b.className, extraClass].filter(Boolean).join(' '),
    ...(b.style ? { style: b.style } : {}),
  }
}
