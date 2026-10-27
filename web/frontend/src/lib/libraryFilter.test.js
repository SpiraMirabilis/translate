import { describe, it, expect } from 'vitest'
import {
  applyFilters, buildShelves, libraryStats, tagsWithLanguage, axisChips,
  continueReading, hasActiveFilters, sortTags,
} from './libraryFilter'

const NOW = new Date('2026-10-11T12:00:00Z').getTime()
const iso = (hoursAgo) => new Date(NOW - hoursAgo * 3600 * 1000).toISOString()

const books = [
  { id: 1, title: 'Mirror of the Divine Emperor', source_title: '神帝之镜', author: 'A', description: 'A xianxia saga',
    tags: ['xianxia', 'female protagonist', 'no romance'], status: 'ongoing', source_language: 'zh',
    chapter_count: 1754, created_date: iso(24 * 400), last_chapter_date: iso(3) },
  { id: 2, title: 'Genius Warlock', author: 'B', description: 'Western fantasy',
    tags: ['western fantasy', 'male protagonist'], status: 'completed', source_language: 'ko',
    chapter_count: 369, created_date: iso(24 * 200), last_chapter_date: iso(24 * 90) },
  { id: 3, title: 'New Arrival', author: 'C', description: '',
    tags: ['xianxia', 'male protagonist', "editor's choice"], status: 'ongoing-trial', source_language: 'zh',
    chapter_count: 12, created_date: iso(24 * 3), last_chapter_date: iso(36) },
]
const ids = (list) => list.map(b => b.id)

describe('tagsWithLanguage', () => {
  it('prepends the language tag once', () => {
    expect(tagsWithLanguage(books[1])).toEqual(['korean', 'western fantasy', 'male protagonist'])
    expect(tagsWithLanguage({ tags: ['Korean'], source_language: 'kr' })).toEqual(['Korean'])
    expect(tagsWithLanguage({ tags: ['x'], source_language: null })).toEqual(['x'])
  })
})

describe('applyFilters', () => {
  it('returns everything for empty filters', () => {
    expect(ids(applyFilters(books, {}))).toEqual([1, 2, 3])
    expect(hasActiveFilters({ q: ' ', tags: [], status: '', lang: '' })).toBe(false)
  })
  it('matches every query word across title/author/description/tags, case-insensitively', () => {
    expect(ids(applyFilters(books, { q: 'MIRROR' }))).toEqual([1])
    expect(ids(applyFilters(books, { q: '神帝' }))).toEqual([1])
    expect(ids(applyFilters(books, { q: 'xianxia' }))).toEqual([1, 3])
    expect(ids(applyFilters(books, { q: 'xianxia saga' }))).toEqual([1])
    expect(ids(applyFilters(books, { q: 'nothing here' }))).toEqual([])
  })
  it('requires every selected tag (AND), language tags included', () => {
    expect(ids(applyFilters(books, { tags: ['xianxia'] }))).toEqual([1, 3])
    expect(ids(applyFilters(books, { tags: ['xianxia', 'female protagonist'] }))).toEqual([1])
    expect(ids(applyFilters(books, { tags: ['korean'] }))).toEqual([2])
  })
  it('groups ongoing-trial under ongoing', () => {
    expect(ids(applyFilters(books, { status: 'ongoing' }))).toEqual([1, 3])
    expect(ids(applyFilters(books, { status: 'completed' }))).toEqual([2])
  })
  it('filters by language', () => {
    expect(ids(applyFilters(books, { lang: 'ko' }))).toEqual([])  // lang is the tag, not the code
    expect(ids(applyFilters(books, { lang: 'korean' }))).toEqual([2])
  })
})

describe('axisChips', () => {
  it('lists vocabulary tags in vocabulary order with counts, skipping unused ones', () => {
    expect(axisChips(books, 'genre')).toEqual([
      { tag: 'xianxia', count: 2 }, { tag: 'western fantasy', count: 1 },
    ])
    expect(axisChips(books, 'romance')).toEqual([{ tag: 'no romance', count: 1 }])
  })
  it('sortTags follows the vocabulary, unknowns last', () => {
    expect(sortTags(['harry potter', 'male protagonist', 'xianxia'])).toEqual(['xianxia', 'male protagonist', 'harry potter'])
  })
})

describe('shelves', () => {
  it('builds updated / new / picks rows', () => {
    const s = buildShelves(books, {}, NOW)
    expect(ids(s.updated)).toEqual([1, 3])   // within 48h, newest first
    expect(ids(s.fresh)).toEqual([3])        // new AND updated: on both shelves
    expect(ids(s.picks)).toEqual([3])        // showcase: never deduped
    expect(s.continue).toEqual([])
  })
  it('New this month skips books already on Continue reading', () => {
    const quiet = { ...books[2], id: 4, last_chapter_date: iso(24 * 10) }
    expect(ids(buildShelves([...books, quiet], {}, NOW).fresh)).toEqual([3, 4])
    expect(ids(buildShelves([...books, quiet], { 3: 1 }, NOW).fresh)).toEqual([4])
  })
  it('continueReading reads both progress shapes and skips unknown books', () => {
    const progress = { 1: { chapter: 45, scrollRatio: 0.3 }, '2': 10, 99: 5 }
    expect(continueReading(books, progress)).toEqual([
      { book: books[0], chapter: 45 }, { book: books[1], chapter: 10 },
    ])
  })
})

describe('libraryStats', () => {
  it('sums chapters and counts books updated this week', () => {
    expect(libraryStats(books, NOW)).toEqual({ books: 3, chapters: 2135, updatedThisWeek: 2 })
  })
})
