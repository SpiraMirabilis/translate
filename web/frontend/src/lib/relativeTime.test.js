import { describe, it, expect } from 'vitest'
import { relativeTime } from './relativeTime'

const NOW = new Date('2026-10-11T12:00:00Z').getTime()
const ago = (ms) => new Date(NOW - ms).toISOString()

describe('relativeTime', () => {
  it('formats compact buckets', () => {
    expect(relativeTime(ago(10_000), NOW)).toBe('just now')
    expect(relativeTime(ago(5 * 60_000), NOW)).toBe('5m ago')
    expect(relativeTime(ago(3 * 3_600_000), NOW)).toBe('3h ago')
    expect(relativeTime(ago(2 * 86_400_000), NOW)).toBe('2d ago')
    expect(relativeTime(ago(21 * 86_400_000), NOW)).toBe('3w ago')
    expect(relativeTime(ago(100 * 86_400_000), NOW)).toBe('3mo ago')
    expect(relativeTime(ago(800 * 86_400_000), NOW)).toBe('2y ago')
  })
  it('is empty for missing or bad input', () => {
    expect(relativeTime(null, NOW)).toBe('')
    expect(relativeTime('garbage', NOW)).toBe('')
  })
})
