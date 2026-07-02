// editorHighlights — pure helpers, runs under the default node environment.
import { describe, it, expect } from 'vitest'
import {
  trimEmptyLines, pinyinToMarked, buildMatcher, highlightSegments,
  applySearchHighlights,
} from './editorHighlights'

describe('trimEmptyLines', () => {
  it('strips leading and trailing blank lines only', () => {
    expect(trimEmptyLines(['', '  ', 'a', '', 'b', '', ''])).toEqual(['a', '', 'b'])
  })

  it('handles all-blank and empty arrays', () => {
    expect(trimEmptyLines(['', '  '])).toEqual([])
    expect(trimEmptyLines([])).toEqual([])
  })
})

describe('pinyinToMarked', () => {
  it('places tone marks on the right vowel', () => {
    expect(pinyinToMarked('ni3 hao3')).toBe('nǐ hǎo')
    expect(pinyinToMarked('zhong1 guo2')).toBe('zhōng guó')   // no a/e/ou → last vowel
    expect(pinyinToMarked('dou1')).toBe('dōu')                // 'ou' → mark the o
    expect(pinyinToMarked('xie4')).toBe('xiè')                // 'e' wins over i
  })

  it('converts u: to ü and marks it', () => {
    expect(pinyinToMarked('lu:4')).toBe('lǜ')
  })

  it('drops the neutral tone (5)', () => {
    expect(pinyinToMarked('ma5')).toBe('ma')
  })

  it('preserves capitalization', () => {
    expect(pinyinToMarked('Zhang1 Yu3')).toBe('Zhāng Yǔ')
  })

  it('passes through syllables without tone numbers', () => {
    expect(pinyinToMarked('hello world')).toBe('hello world')
  })
})

describe('buildMatcher', () => {
  const entities = [
    { untranslated: '张羽', translation: 'Zhang Yu', category: 'characters' },
    { untranslated: '青云宗', translation: 'Azure Cloud Sect', category: 'organizations' },
    { untranslated: '云', translation: 'Cloud', category: 'places' },        // 1 char → skipped
    { untranslated: '张羽', translation: 'Zhang Yu dup', category: 'characters' }, // dup key → skipped
  ]

  it('skips short keys and case-insensitive duplicates', () => {
    const m = buildMatcher(entities, 'untranslated')
    expect(m.list.map(e => e.text)).toEqual(['青云宗', '张羽']) // longest first
    expect(m.lookup.get('张羽').translation).toBe('Zhang Yu')  // first wins
  })

  it('builds a global regex; case-insensitive only for translation field', () => {
    expect(buildMatcher(entities, 'untranslated').regex.flags).toBe('g')
    expect(buildMatcher(entities, 'translation').regex.flags).toBe('gi')
  })

  it('escapes regex metacharacters in entity text', () => {
    const m = buildMatcher([{ untranslated: 'a+b', translation: 'x (y)' }], 'translation')
    expect(m.regex.test('x (y)')).toBe(true)
  })

  it('returns a null regex for an empty list', () => {
    expect(buildMatcher([], 'untranslated').regex).toBeNull()
  })
})

describe('highlightSegments', () => {
  const matcher = buildMatcher(
    [{ untranslated: '张羽', translation: 'Zhang Yu', category: 'characters' }],
    'untranslated'
  )

  it('splits a line into plain and entity segments', () => {
    const segs = highlightSegments('大家看着张羽离开', matcher)
    expect(segs).toEqual([
      { text: '大家看着' },
      { text: '张羽', entity: expect.objectContaining({ translation: 'Zhang Yu' }) },
      { text: '离开' },
    ])
  })

  it('matches translation text case-insensitively', () => {
    const m = buildMatcher([{ untranslated: '张羽', translation: 'Zhang Yu' }], 'translation')
    const segs = highlightSegments('then ZHANG YU left', m)
    expect(segs[1].text).toBe('ZHANG YU')
    expect(segs[1].entity.untranslated).toBe('张羽')
  })

  it('falls back to nbsp for empty lines and passes lines with no matcher through', () => {
    expect(highlightSegments('', matcher)).toEqual([{ text: ' ' }])
    expect(highlightSegments('abc', { regex: null })).toEqual([{ text: 'abc' }])
  })
})

describe('applySearchHighlights', () => {
  it('marks match ranges, flagging the active one', () => {
    const text = 'foo bar foo'
    const matches = [
      { col: 0, length: 3, field: 't' },
      { col: 8, length: 3, field: 't' },
    ]
    const parts = applySearchHighlights(text, matches, matches[1])
    expect(parts).toEqual([
      { text: 'foo', search: true, active: false },
      { text: ' bar ' },
      { text: 'foo', search: true, active: true },
    ])
  })

  it('returns whole text when there are no matches', () => {
    expect(applySearchHighlights('abc', [], null)).toEqual([{ text: 'abc' }])
  })
})
