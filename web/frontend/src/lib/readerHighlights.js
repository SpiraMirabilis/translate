/**
 * readerHighlights — marking glossary terms inside already-rendered chapter HTML.
 *
 * The reader renders Markdown to an HTML string and injects it with
 * dangerouslySetInnerHTML, so highlights cannot be woven in during render the
 * way the Chapter Editor does it (see lib/editorHighlights). They are applied
 * to the live DOM afterwards instead: only text nodes are touched, so no markup
 * can be broken, and links, code spans and footnote markers are skipped so a
 * term inside one is left alone.
 *
 * Only terms carrying a note are ever marked — the note is the whole point of
 * the hover, and marking every glossary term would stripe the page.
 */

const HL_CLASS = 'term-note'
// Never highlight inside these: a term inside a link or a footnote marker would
// fight with an existing interaction, and code spans are verbatim by definition.
const SKIP_SELECTOR = `a, code, pre, sup, .footnote-ref, .${HL_CLASS}`

const WORD_CHAR = /[A-Za-z0-9]/

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Build a matcher over the terms worth marking.
 *
 * Matching is CASE-SENSITIVE and boundary-checked for Latin-script terms: the
 * glossary is full of renderings like "Master", "Yao" or "Gold" whose lowercase
 * or embedded forms are ordinary English, and a false highlight in prose is
 * worse than a missed one. Chinese source forms have no word boundaries, so the
 * check applies per edge — only where the term's own edge is a word character.
 *
 * @param terms  rows from GET .../chapters/{n}/terms
 * @param opts   { charactersOnly, genderedCategories }
 */
export function buildTermMatcher(terms, { charactersOnly = false, genderedCategories = [] } = {}) {
  if (!terms || terms.length === 0) return null
  const gendered = new Set(genderedCategories)

  const keys = new Map()   // matched string -> term
  for (const term of terms) {
    if (!term.note) continue
    if (charactersOnly && !gendered.has(term.category)) continue
    // Both renderings: the reader may be showing the translation, the source,
    // or both at once.
    for (const key of [term.translation, term.untranslated]) {
      if (!key || key.length < 2) continue
      if (!keys.has(key)) keys.set(key, term)
    }
  }
  if (keys.size === 0) return null

  // Longest first so "Lady Golden Serpent" wins over "Lady Gold".
  const sorted = [...keys.keys()].sort((a, b) => b.length - a.length)
  const regex = new RegExp(sorted.map(escapeRe).join('|'), 'g')
  return { regex, lookup: keys }
}

function boundariesOk(text, start, end, key) {
  if (WORD_CHAR.test(key[0])) {
    const before = text[start - 1]
    if (before && WORD_CHAR.test(before)) return false
  }
  if (WORD_CHAR.test(key[key.length - 1])) {
    const after = text[end]
    if (after && WORD_CHAR.test(after)) return false
  }
  return true
}

function highlightTextNode(node, matcher, colorFor) {
  const text = node.nodeValue
  const { regex, lookup } = matcher
  regex.lastIndex = 0

  let frag = null
  let cursor = 0
  let match
  while ((match = regex.exec(text)) !== null) {
    const key = match[0]
    const start = match.index
    const end = start + key.length
    if (!boundariesOk(text, start, end, key)) continue
    const term = lookup.get(key)
    if (!term) continue

    frag = frag || document.createDocumentFragment()
    if (start > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, start)))

    const span = document.createElement('span')
    span.className = HL_CLASS
    span.textContent = key
    span.dataset.note = term.note
    span.dataset.term = term.translation || key
    span.style.borderBottom = `1px dotted ${colorFor(term)}`
    span.style.cursor = 'help'
    frag.appendChild(span)
    cursor = end
  }

  if (!frag) return
  if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)))
  node.parentNode.replaceChild(frag, node)
}

/** Mark every matching term inside `root`. Idempotent-safe: already-marked spans are skipped. */
export function applyTermHighlights(root, matcher, colorFor = () => 'currentColor') {
  if (!root || !matcher) return
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || node.nodeValue.length < 2) return NodeFilter.FILTER_REJECT
      const parent = node.parentElement
      if (!parent || parent.closest(SKIP_SELECTOR)) return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })
  // Collect first: replacing nodes during the walk invalidates it.
  const targets = []
  while (walker.nextNode()) targets.push(walker.currentNode)
  for (const node of targets) highlightTextNode(node, matcher, colorFor)
}

/** Undo applyTermHighlights, putting the original text nodes back. */
export function clearTermHighlights(root) {
  if (!root) return
  const spans = root.querySelectorAll(`span.${HL_CLASS}`)
  for (const span of spans) {
    const parent = span.parentNode
    if (!parent) continue
    parent.replaceChild(document.createTextNode(span.textContent), span)
    parent.normalize()
  }
}
