export const DEFAULT_CATEGORIES = [
  'characters', 'places', 'organizations', 'abilities',
  'titles', 'equipment', 'creatures'
]

const DEFAULT_COLORS = {
  characters:    'badge-indigo',
  places:        'badge-emerald',
  organizations: 'badge-amber',
  abilities:     'badge-rose',
  titles:        'badge-slate',
  equipment:     'badge-slate',
  creatures:     'badge-indigo',
}

const EXTRA_COLORS = [
  'badge-violet', 'badge-cyan', 'badge-orange',
  'badge-lime', 'badge-fuchsia', 'badge-teal'
]

export function getCatColor(category) {
  if (DEFAULT_COLORS[category]) return DEFAULT_COLORS[category]
  let hash = 0
  for (let i = 0; i < category.length; i++)
    hash = ((hash << 5) - hash) + category.charCodeAt(i)
  return EXTRA_COLORS[Math.abs(hash) % EXTRA_COLORS.length]
}
