// categories — pure helpers, runs under the default node environment.
import { describe, it, expect } from 'vitest'
import {
  DEFAULT_CATEGORIES, DEFAULT_GENDERED_CATEGORIES, CATEGORY_COLORS,
  isGenderedCategory, getCatBadge, catBadgeProps,
} from './categories'

describe('constants', () => {
  it('every default category has highlight colors', () => {
    for (const cat of DEFAULT_CATEGORIES) {
      expect(CATEGORY_COLORS[cat]).toEqual({
        bg: expect.stringMatching(/^rgba\(/),
        border: expect.stringMatching(/^rgba\(/),
      })
    }
  })

  it('gendered defaults are a subset of the default categories', () => {
    for (const cat of DEFAULT_GENDERED_CATEGORIES) {
      expect(DEFAULT_CATEGORIES).toContain(cat)
    }
  })
})

describe('isGenderedCategory', () => {
  it('falls back to the characters default without an attribute map', () => {
    expect(isGenderedCategory('characters')).toBe(true)
    expect(isGenderedCategory('places')).toBe(false)
    expect(isGenderedCategory('characters', null)).toBe(true)
  })

  it('honors an explicit per-category attribute map', () => {
    expect(isGenderedCategory('places', { places: ['gender'] })).toBe(true)
    expect(isGenderedCategory('characters', { characters: [] })).toBe(false)
    expect(isGenderedCategory('characters', { characters: null })).toBe(false)
  })

  it('falls back to the default for categories absent from the map', () => {
    expect(isGenderedCategory('characters', { places: ['gender'] })).toBe(true)
    expect(isGenderedCategory('spells', { places: ['gender'] })).toBe(false)
  })

  it('returns false for a missing category', () => {
    expect(isGenderedCategory('')).toBe(false)
    expect(isGenderedCategory(null)).toBe(false)
  })
})

describe('getCatBadge', () => {
  it('uses a named badge class for default categories (no inline style)', () => {
    expect(getCatBadge('characters')).toEqual({ className: 'badge badge-indigo' })
    expect(getCatBadge('places').className).toBe('badge badge-emerald')
    expect(getCatBadge('places').style).toBeUndefined()
  })

  it('derives a deterministic hsl style for dynamic categories', () => {
    const a = getCatBadge('life simulator terms')
    const b = getCatBadge('life simulator terms')
    expect(a).toEqual(b)
    expect(a.className).toBe('badge')
    expect(a.style.backgroundColor).toMatch(/^hsl\(\d+ 50% 20%\)$/)
    expect(a.style.color).toMatch(/^hsl\(\d+ 60% 70%\)$/)
  })
})

describe('catBadgeProps', () => {
  it('joins extra classes and omits style for default categories', () => {
    expect(catBadgeProps('characters', 'ml-2')).toEqual({
      className: 'badge badge-indigo ml-2',
    })
    expect(catBadgeProps('characters')).toEqual({ className: 'badge badge-indigo' })
  })

  it('spreads the style for dynamic categories', () => {
    const props = catBadgeProps('spells', 'x')
    expect(props.className).toBe('badge x')
    expect(props.style).toBeDefined()
  })
})
