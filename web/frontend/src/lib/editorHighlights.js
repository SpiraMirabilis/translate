/**
 * editorHighlights — pure text/highlight helpers for the Chapter Editor.
 * No React: syllable/pinyin tone-mark conversion, entity matcher building,
 * line segmentation for entity highlights, and search-highlight splitting.
 */

// ── Trim empty lines from start/end of an array ─────────────────────
export function trimEmptyLines(lines) {
  let start = 0
  while (start < lines.length && !lines[start].trim()) start++
  let end = lines.length
  while (end > start && !lines[end - 1].trim()) end--
  return lines.slice(start, end)
}

// ── Pinyin tone number → tone mark conversion ───────────────────────
const TONE_MARKS = {
  a: ['ā','á','ǎ','à'], e: ['ē','é','ě','è'], i: ['ī','í','ǐ','ì'],
  o: ['ō','ó','ǒ','ò'], u: ['ū','ú','ǔ','ù'], ü: ['ǖ','ǘ','ǚ','ǜ'],
}

function syllableToMarked(s) {
  const m = s.match(/^([a-zA-ZüÜ]+?)([1-5])$/)
  if (!m) return s
  let [, base, tone] = m
  const wasCapitalized = base[0] === base[0].toUpperCase()
  base = base.toLowerCase()
  tone = parseInt(tone)
  if (tone === 5) return base
  const vowels = 'aeiouü'
  let idx = -1
  if (base.includes('a')) idx = base.indexOf('a')
  else if (base.includes('e')) idx = base.indexOf('e')
  else if (base.includes('ou')) idx = base.indexOf('o')
  else {
    for (let i = base.length - 1; i >= 0; i--) {
      if (vowels.includes(base[i])) { idx = i; break }
    }
  }
  if (idx === -1) return base
  const ch = base[idx]
  const marked = TONE_MARKS[ch]?.[tone - 1]
  if (!marked) return base
  let result = base.slice(0, idx) + marked + base.slice(idx + 1)
  if (wasCapitalized) result = result[0].toUpperCase() + result.slice(1)
  return result
}

export function pinyinToMarked(pinyin) {
  const normalized = pinyin.replace(/u:/g, 'ü')
  return normalized.split(/\s+/).map(syllableToMarked).join(' ')
}


// ── Entity highlighting helpers ──────────────────────────────────────

/**
 * Build a matcher object from entities.
 * Returns { lookup (Map), regex (RegExp), list (array) } for fast matching.
 * `field` is 'untranslated' for Chinese matching, 'translation' for English matching.
 */
export function buildMatcher(entities, field) {
  const seen = new Set()
  const list = []
  for (const ent of entities) {
    const key = ent[field]
    if (!key || key.length < 2 || seen.has(key.toLowerCase())) continue
    seen.add(key.toLowerCase())
    list.push({
      text: key,
      lower: key.toLowerCase(),
      translation: ent.translation,
      untranslated: ent.untranslated,
      category: ent.category,
    })
  }
  // Sort by length descending so longest matches win
  list.sort((a, b) => b.text.length - a.text.length)

  if (!list.length) return { lookup: new Map(), regex: null, list }

  // Build a single regex from all entity texts (longest first for greedy matching)
  const escaped = list.map(m => m.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const regex = new RegExp(`(${escaped.join('|')})`, 'g' + (field === 'translation' ? 'i' : ''))

  // Build a lookup map (lowercased key -> entity) for O(1) match identification
  const lookup = new Map()
  for (const m of list) {
    const k = m.text.toLowerCase()
    if (!lookup.has(k)) lookup.set(k, m)
  }

  return { lookup, regex, list }
}

/**
 * Split a line into segments of plain text and entity matches.
 * Returns [{ text, entity? }] where entity is the matched entity info or null.
 * Uses a precompiled regex for fast matching instead of scanning all entities.
 */
export function highlightSegments(line, matcher, _caseInsensitive = false) {
  if (!line || !matcher.regex) return [{ text: line || '\u00A0' }]

  const { regex, lookup } = matcher
  const segments = []
  let lastIndex = 0

  // Reset regex state (it has the 'g' flag)
  regex.lastIndex = 0
  let match
  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: line.slice(lastIndex, match.index) })
    }
    const matched = match[0]
    const entity = lookup.get(matched.toLowerCase())
    segments.push({ text: matched, entity: entity || null })
    lastIndex = regex.lastIndex
  }

  if (lastIndex < line.length) {
    segments.push({ text: line.slice(lastIndex) })
  }

  return segments.length ? segments : [{ text: line || '\u00A0' }]
}

// ── Apply search highlight marks to text ──────────────────────────────
export function applySearchHighlights(textContent, searchMatches, activeMatch) {
  if (!searchMatches || searchMatches.length === 0) return [{ text: textContent }]
  // Sort by col ascending
  const sorted = [...searchMatches].sort((a, b) => a.col - b.col)
  const parts = []
  let pos = 0
  for (const m of sorted) {
    if (m.col > pos) parts.push({ text: textContent.slice(pos, m.col) })
    const isActive = activeMatch && m.col === activeMatch.col && m.length === activeMatch.length && m.field === activeMatch.field
    parts.push({ text: textContent.slice(m.col, m.col + m.length), search: true, active: isActive })
    pos = m.col + m.length
  }
  if (pos < textContent.length) parts.push({ text: textContent.slice(pos) })
  return parts
}
