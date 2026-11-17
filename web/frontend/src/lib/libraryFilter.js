// Pure helpers behind the public Library page: filtering, shelving, stats.
// Everything here takes the /api/public/books payload and returns arrays —
// no React, so it is unit-tested directly (libraryFilter.test.js).
import vocab from '../../../../tag_vocab.json'
import { hasEditorsChoice } from '../utils/specialTags'

// Source-language code → display tag. Colloquial variants (kr/jp) sit beside
// the ISO codes so a book tags correctly however it was created.
export const LANGUAGE_TAGS = {
  en: 'english',
  zh: 'chinese',
  ru: 'russian',
  ko: 'korean',   kr: 'korean',
  ja: 'japanese', jp: 'japanese',
}

export const NEW_BOOK_DAYS = 31        // "New" badge / New shelf
export const RECENT_UPDATE_HOURS = 48  // Recently-updated shelf
export const WEEK_MS = 7 * 24 * 3600 * 1000

export const AXES = vocab.axes
const CANONICAL_ORDER = new Map(
  Object.values(vocab.axes).flat().map((t, i) => [t, i]),
)

export function languageTag(book) {
  return LANGUAGE_TAGS[(book.source_language || '').toLowerCase()] || null
}

// A book's own tags with the language tag prepended when not already present.
export function tagsWithLanguage(book) {
  const tags = book.tags || []
  const lang = languageTag(book)
  if (!lang || tags.some(t => t.toLowerCase() === lang)) return tags
  return [lang, ...tags]
}

export function isNewlyAdded(createdDate, now = Date.now()) {
  if (!createdDate) return false
  const created = new Date(createdDate).getTime()
  if (isNaN(created)) return false
  return now - created < NEW_BOOK_DAYS * 24 * 3600 * 1000
}

export function hasRecentUpdate(lastChapterDate, now = Date.now()) {
  if (!lastChapterDate) return false
  const updated = new Date(lastChapterDate).getTime()
  if (isNaN(updated)) return false
  return now - updated < RECENT_UPDATE_HOURS * 3600 * 1000
}

// ---- filtering -------------------------------------------------------------

export const EMPTY_FILTERS = { q: '', tags: [], status: '', lang: '' }

export function hasActiveFilters(f) {
  return !!(f.q?.trim() || (f.tags && f.tags.length) || f.status || f.lang)
}

const norm = (s) => (s || '').toString().toLowerCase()

function matchesQuery(book, q) {
  const words = norm(q).split(/\s+/).filter(Boolean)
  if (!words.length) return true
  const hay = [book.title, book.source_title, book.nu_title, book.author, book.description, ...(book.tags || [])]
    .map(norm).join(' \n ')
  return words.every(w => hay.includes(w))
}

// Status groups: the chip says "Ongoing" but a trial run is ongoing too.
const STATUS_GROUPS = {
  ongoing:   ['ongoing', 'ongoing-trial'],
  completed: ['completed'],
  hiatus:    ['hiatus'],
  dropped:   ['dropped'],
}

export function applyFilters(books, filters) {
  const f = { ...EMPTY_FILTERS, ...filters }
  const wantTags = (f.tags || []).map(norm).filter(Boolean)
  const statuses = f.status ? (STATUS_GROUPS[f.status] || [f.status]) : null
  return books.filter(b => {
    if (statuses && !statuses.includes(b.status || 'ongoing')) return false
    if (f.lang && languageTag(b) !== norm(f.lang)) return false
    if (wantTags.length) {
      const have = new Set(tagsWithLanguage(b).map(norm))
      if (!wantTags.every(t => have.has(t))) return false
    }
    return matchesQuery(b, f.q)
  })
}

// Tag → number of books carrying it (language tags included).
export function tagCounts(books) {
  const counts = new Map()
  for (const b of books) {
    for (const t of new Set(tagsWithLanguage(b).map(norm))) {
      counts.set(t, (counts.get(t) || 0) + 1)
    }
  }
  return counts
}

// The filter vocabulary: NovelUpdates' genres and tags (/api/public/nu-tags.json,
// `{genres: [{id, name}], tags: [...]}`), plus our house tags — canonical
// tag_vocab.json entries NU has no equivalent for (litrpg, no romance, sect
// life, …). Until the NU list has loaded (or if it 404s before the first
// fetch), tag_vocab.json stands in: its genre axis as genres, the rest as tags.
// Fandom tags and "editor's choice" (it has its own shelf) never get a chip.
const HOUSE_TAGS = Object.entries(vocab.axes)
  .filter(([axis]) => axis !== 'genre' && axis !== 'special')
  .flatMap(([, tags]) => tags)

export function filterVocab(nu) {
  const names = (list) => (list || []).map(i => norm(typeof i === 'string' ? i : i?.name)).filter(Boolean)
  const genres = names(nu?.genres)
  if (!genres.length) return { genres: [...vocab.axes.genre], tags: [...HOUSE_TAGS] }
  const genreSet = new Set(genres)
  const tags = [...new Set([...names(nu.tags), ...HOUSE_TAGS])].filter(t => !genreSet.has(t))
  return { genres, tags }
}

// Chips for a vocabulary list, most-carried first (ties alphabetical),
// dropping tags no book carries (min 1) so the bar never offers an empty result.
export function vocabChips(books, list, { min = 1, counts = tagCounts(books) } = {}) {
  return [...new Set(list)]
    .map(tag => ({ tag, count: counts.get(tag) || 0 }))
    .filter(c => c.count >= min)
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
}

// Both chip groups at once (one tag count pass).
export function filterChips(books, nu) {
  const v = filterVocab(nu)
  const counts = tagCounts(books)
  return { genres: vocabChips(books, v.genres, { counts }), tags: vocabChips(books, v.tags, { counts }) }
}

export function languageChips(books) {
  const counts = new Map()
  for (const b of books) {
    const l = languageTag(b)
    if (l) counts.set(l, (counts.get(l) || 0) + 1)
  }
  return [...counts].map(([tag, count]) => ({ tag, count })).sort((a, b) => b.count - a.count)
}

// Sort key for a tag list on a card/overlay: vocabulary order, unknowns last.
export function sortTags(tags) {
  return [...tags].sort((a, b) =>
    (CANONICAL_ORDER.get(norm(a)) ?? 1e6) - (CANONICAL_ORDER.get(norm(b)) ?? 1e6) || a.localeCompare(b))
}

// ---- shelves ---------------------------------------------------------------

// reader-progress entries are a bare chapter number (legacy) or
// { chapter, scrollRatio }. Read both forever.
export const progressChapter = (entry) =>
  typeof entry === 'number' ? entry : (entry?.chapter ?? null)

const byDateDesc = (key) => (a, b) =>
  new Date(b[key] || 0).getTime() - new Date(a[key] || 0).getTime()

// Books the reader has opened, most recently *updated* first (progress has no
// timestamp; the book with new chapters is the one worth surfacing).
export function continueReading(books, progress, { limit = 12 } = {}) {
  if (!progress) return []
  const out = []
  for (const b of books) {
    const ch = progressChapter(progress[b.id] ?? progress[String(b.id)])
    if (ch && ch > 0) out.push({ book: b, chapter: ch })
  }
  out.sort((x, y) => byDateDesc('last_chapter_date')(x.book, y.book))
  return out.slice(0, limit)
}

// New this month skips books already on Continue reading (the reader has found
// them), but not ones on Recently updated: a new book that also got chapters
// belongs on both — "new" and "updated" answer different questions. Editor's
// choice stays complete — it is a showcase, not a feed.
export function buildShelves(books, progress, now = Date.now()) {
  const cont = continueReading(books, progress)
  const updated = books.filter(b => hasRecentUpdate(b.last_chapter_date, now))
    .sort(byDateDesc('last_chapter_date'))
  const reading = new Set(cont.map(c => c.book.id))
  const fresh = books.filter(b => !reading.has(b.id) && isNewlyAdded(b.created_date, now))
    .sort(byDateDesc('created_date'))
  const picks = books.filter(b => hasEditorsChoice(b.tags || []))
  return { continue: cont, updated, fresh, picks }
}

// ---- header stats ----------------------------------------------------------

export function libraryStats(books, now = Date.now()) {
  let chapters = 0, updatedThisWeek = 0
  for (const b of books) {
    chapters += b.chapter_count || 0
    const t = new Date(b.last_chapter_date || 0).getTime()
    if (t && now - t < WEEK_MS) updatedThisWeek++
  }
  return { books: books.length, chapters, updatedThisWeek }
}
