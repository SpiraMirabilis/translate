import { describe, it, expect } from 'vitest'
import { importBookTags } from './novelupdates'

const vocab = {
  genres: ['Xianxia', 'Supernatural', 'School Life', 'Fantasy'],
  tags: ['Weak to Strong', 'Reincarnation', 'Female Protagonist', 'Historical'],
}

describe('importBookTags', () => {
  it('matches by name across case and hyphens, genre before tag', () => {
    const r = importBookTags(['xianxia', 'weak-to-strong', 'school life', 'made up'], vocab)
    expect(r.genres).toEqual(['Xianxia', 'School Life'])
    expect(r.tags).toEqual(['Weak to Strong'])
    expect(r.unmatchedTags).toEqual(['made up'])
    expect(r.added).toEqual(['Xianxia', 'Weak to Strong', 'School Life'])
  })

  it('tries the raw tag before its alias, then the alias', () => {
    // rebirth is a tag_vocab alias of reincarnation; supernatural matches NU directly
    const r = importBookTags(['supernatural', 'rebirth'], vocab)
    expect(r.genres).toEqual(['Supernatural'])
    expect(r.tags).toEqual(['Reincarnation'])
  })

  it('keeps what the draft holds and adds nothing twice', () => {
    const r = importBookTags(['xianxia', "editor's choice"], vocab, { genres: ['Xianxia'], tags: ['Historical'] })
    expect(r.genres).toEqual(['Xianxia'])
    expect(r.tags).toEqual(['Historical'])
    expect(r.added).toEqual([])
    expect(r.unmatchedTags).toEqual([])
  })
})
