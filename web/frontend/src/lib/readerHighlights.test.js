/**
 * @vitest-environment jsdom
 *
 * readerHighlights touches the DOM directly (the chapter body is injected HTML),
 * so these run in jsdom rather than the default node environment.
 */
import { describe, it, expect } from 'vitest'
import { buildTermMatcher, applyTermHighlights, clearTermHighlights } from './readerHighlights'

const TERMS = [
  { id: 1, category: 'characters', untranslated: '苏络', translation: 'Su Luo',
    note: 'Protagonist.', occurrences: 3 },
  { id: 2, category: 'characters', untranslated: '金柳', translation: 'Jin Liu',
    note: 'Her elder brother.', occurrences: 1 },
  { id: 3, category: 'places', untranslated: '青云宗', translation: 'Azure Cloud Sect',
    note: 'The sect she joins.', occurrences: 1 },
  { id: 4, category: 'titles', untranslated: '大师', translation: 'Master',
    note: 'Form of address.', occurrences: 1 },
  { id: 5, category: 'places', untranslated: '落雨村', translation: 'Fallrain Village',
    occurrences: 1 },   // no note — never highlighted
]
const OPTS = { genderedCategories: ['characters'] }

function body(html) {
  const root = document.createElement('div')
  root.innerHTML = html
  return root
}

const marks = (root) => [...root.querySelectorAll('span.term-note')].map(s => s.textContent)

describe('buildTermMatcher', () => {
  it('takes only terms that carry a note', () => {
    const m = buildTermMatcher(TERMS, OPTS)
    expect(m.lookup.has('Su Luo')).toBe(true)
    expect(m.lookup.has('Fallrain Village')).toBe(false)
  })

  it('charactersOnly keeps the gendered categories only', () => {
    const m = buildTermMatcher(TERMS, { ...OPTS, charactersOnly: true })
    expect(m.lookup.has('Su Luo')).toBe(true)
    expect(m.lookup.has('Azure Cloud Sect')).toBe(false)
    expect(m.lookup.has('Master')).toBe(false)
  })

  it('is null when nothing qualifies', () => {
    expect(buildTermMatcher([{ category: 'places', translation: 'X' }], OPTS)).toBe(null)
    expect(buildTermMatcher([], OPTS)).toBe(null)
  })
})

describe('applyTermHighlights', () => {
  it('marks translated and source forms and carries the note', () => {
    const root = body('<p>Su Luo bowed. 苏络行礼。</p>')
    applyTermHighlights(root, buildTermMatcher(TERMS, OPTS))
    expect(marks(root)).toEqual(['Su Luo', '苏络'])
    expect(root.querySelector('span.term-note').dataset.note).toBe('Protagonist.')
    expect(root.textContent).toBe('Su Luo bowed. 苏络行礼。')
  })

  it('is case-sensitive — the lowercase word is ordinary prose', () => {
    const root = body('<p>The master called Master over.</p>')
    applyTermHighlights(root, buildTermMatcher(TERMS, OPTS))
    expect(marks(root)).toEqual(['Master'])
  })

  it('respects word boundaries on Latin terms', () => {
    const root = body('<p>Su Luo, not Su Luoxia or XSu Luo.</p>')
    applyTermHighlights(root, buildTermMatcher(TERMS, OPTS))
    expect(marks(root)).toEqual(['Su Luo'])
  })

  it('prefers the longest term', () => {
    const overlapping = [
      { id: 1, category: 'places', translation: 'Azure Cloud', untranslated: '青云', note: 'short' },
      { id: 2, category: 'places', translation: 'Azure Cloud Sect', untranslated: '青云宗', note: 'long' },
    ]
    const root = body('<p>Azure Cloud Sect</p>')
    applyTermHighlights(root, buildTermMatcher(overlapping, OPTS))
    expect(marks(root)).toEqual(['Azure Cloud Sect'])
  })

  it('leaves links, code and footnote markers alone', () => {
    const root = body('<p><a href="#x">Su Luo</a> <code>Su Luo</code>' +
                      '<sup class="footnote-ref">Su Luo</sup> Su Luo</p>')
    applyTermHighlights(root, buildTermMatcher(TERMS, OPTS))
    expect(marks(root)).toEqual(['Su Luo'])
  })

  it('does not double-wrap an already highlighted run', () => {
    const matcher = buildTermMatcher(TERMS, OPTS)
    const root = body('<p>Su Luo and Jin Liu.</p>')
    applyTermHighlights(root, matcher)
    applyTermHighlights(root, matcher)
    expect(marks(root)).toEqual(['Su Luo', 'Jin Liu'])
  })
})

describe('clearTermHighlights', () => {
  it('restores the original text', () => {
    const root = body('<p>Su Luo met Jin Liu at the Azure Cloud Sect.</p>')
    const html = root.innerHTML
    applyTermHighlights(root, buildTermMatcher(TERMS, OPTS))
    expect(marks(root).length).toBe(3)
    clearTermHighlights(root)
    expect(root.innerHTML).toBe(html)
    expect(root.querySelector('p').childNodes.length).toBe(1)  // normalized back
  })
})
