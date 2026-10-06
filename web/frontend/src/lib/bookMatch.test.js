import { describe, it, expect } from 'vitest'
import { rankBooks } from './bookMatch'

const books = [
  { id: 4, title: 'Mirror of the Divine Emperor' },
  { id: 35, title: 'The White Snake' },
  { id: 90, title: 'Immortal Path' },
  { id: 9, title: 'Snake Eater' },
  { id: 104, title: 'My Dad Is an Immortal' },
]
const ids = (q) => rankBooks(books, q).map(b => b.id)

describe('rankBooks', () => {
  it('returns everything, in order, for an empty query', () => {
    expect(ids('')).toEqual([4, 35, 90, 9, 104])
    expect(ids('   ')).toEqual([4, 35, 90, 9, 104])
  })
  it('puts an exact id first, then id prefixes', () => {
    expect(ids('9')).toEqual([9, 90])
    expect(ids('10')).toEqual([104])
  })
  it('ranks title prefixes above substrings, case-insensitively', () => {
    expect(ids('snake')).toEqual([9, 35])
    expect(ids('IMMORTAL')).toEqual([90, 104])
  })
  it('matches every word anywhere in "id: title"', () => {
    expect(ids('snake white')).toEqual([35])
    expect(ids('35 snake')).toEqual([35])
  })
  it('returns nothing when nothing matches', () => {
    expect(ids('zzz')).toEqual([])
  })
})
