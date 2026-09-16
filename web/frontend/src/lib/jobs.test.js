import { describe, it, expect } from 'vitest'
import {
  NO_BOOK_KEY, jobKey, jobKeyOf, isActiveStatus, needsAttention,
  jobsReducer, activeJobs, pendingPrompts, isBookRunning,
} from './jobs'

const ws = (msg, seq = 1) => ({ type: 'WS', msg, seq })

describe('keys', () => {
  it('keys by book id, with a slot for the bookless job', () => {
    expect(jobKey(14)).toBe('14')
    expect(jobKey(null)).toBe(NO_BOOK_KEY)
    expect(jobKeyOf({ type: 'progress', book_id: 79 })).toBe('79')
  })

  it('ignores messages that are not job-scoped', () => {
    expect(jobKeyOf({ type: 'activity_log', entry: {} })).toBeNull()
    expect(jobKeyOf(null)).toBeNull()
  })
})

describe('status predicates', () => {
  it('treats every awaiting_* state as active', () => {
    // Queue.jsx used to omit awaiting_json_fix/awaiting_chapter_conflict, so
    // the two pages disagreed about whether anything was running.
    for (const s of ['running', 'waiting', 'awaiting_review', 'awaiting_json_fix',
                     'awaiting_chapter_conflict']) {
      expect(isActiveStatus(s)).toBe(true)
    }
    for (const s of ['idle', 'complete', 'error', undefined]) {
      expect(isActiveStatus(s)).toBe(false)
    }
  })

  it('flags jobs blocked on a human', () => {
    expect(needsAttention({ status: 'awaiting_review' })).toBe(true)
    expect(needsAttention({ status: 'running' })).toBe(false)
  })
})

describe('routing by book', () => {
  it('keeps two books progress separate', () => {
    let s = {}
    s = jobsReducer(s, ws({ type: 'progress', book_id: 14, percent: 10 }, 1))
    s = jobsReducer(s, ws({ type: 'progress', book_id: 79, percent: 90 }, 2))

    expect(s['14'].progress.percent).toBe(10)
    expect(s['79'].progress.percent).toBe(90)
  })

  it('completing one book leaves the other running', () => {
    let s = {}
    s = jobsReducer(s, ws({ type: 'progress', book_id: 14, percent: 10 }, 1))
    s = jobsReducer(s, ws({ type: 'progress', book_id: 79, percent: 90 }, 2))
    s = jobsReducer(s, ws({ type: 'translation_complete', book_id: 14, chapter: 5 }, 3))

    expect(s['14'].status).toBe('complete')
    expect(s['79'].status).toBe('running')
    expect(isBookRunning(s, 79)).toBe(true)
    expect(isBookRunning(s, 14)).toBe(false)
  })

  it('records a prompt against only the book that raised it', () => {
    let s = {}
    s = jobsReducer(s, ws({ type: 'progress', book_id: 79, percent: 5 }, 1))
    s = jobsReducer(s, ws({ type: 'entity_review_needed', book_id: 14, entities: { a: 1 } }, 2))

    expect(s['14'].status).toBe('awaiting_review')
    expect(s['14'].pending_review.entities).toEqual({ a: 1 })
    expect(s['79'].status).toBe('running')
    expect(pendingPrompts(s).map((j) => j.book_id)).toEqual([14])
  })

  it('clears a resolved json fix without touching other books', () => {
    let s = {}
    s = jobsReducer(s, ws({ type: 'json_fix_needed', book_id: 14, raw_response: 'x' }, 1))
    s = jobsReducer(s, ws({ type: 'json_fix_needed', book_id: 79, raw_response: 'y' }, 2))
    s = jobsReducer(s, ws({ type: 'json_fix_resolved', book_id: 14 }, 3))

    expect(s['14'].status).toBe('running')
    expect(s['14'].pending_json_fix).toBeNull()
    expect(s['79'].status).toBe('awaiting_json_fix')
  })

  it('maps an overload pause to waiting', () => {
    const s = jobsReducer({}, ws({ type: 'progress', book_id: 14, phase: 'overloaded' }, 1))
    expect(s['14'].status).toBe('waiting')
  })

  it('drops messages with no book scope', () => {
    const before = { 14: { book_id: 14, status: 'running' } }
    expect(jobsReducer(before, ws({ type: 'activity_log', entry: {} }, 2))).toBe(before)
  })
})

describe('hydration', () => {
  it('adopts the server snapshot and keeps local progress', () => {
    let s = jobsReducer({}, ws({ type: 'progress', book_id: 14, percent: 42 }, 5))
    s = jobsReducer(s, {
      type: 'HYDRATE', seq: 5,
      jobs: [{ book_id: 14, status: 'running', is_running: true }],
    })

    // The snapshot carries no progress ticks; the socket's are still the truth.
    expect(s['14'].progress.percent).toBe(42)
    expect(s['14'].status).toBe('running')
  })

  it('does not let a stale snapshot resurrect a finished job', () => {
    let s = jobsReducer({}, ws({ type: 'translation_complete', book_id: 14, chapter: 3 }, 9))
    s = jobsReducer(s, {
      type: 'HYDRATE', seq: 4,   // response predates the completion we saw
      jobs: [{ book_id: 14, status: 'running', is_running: true }],
    })

    expect(s['14'].status).toBe('complete')
  })

  it('forgets jobs the server no longer reports', () => {
    let s = jobsReducer({}, ws({ type: 'progress', book_id: 14, percent: 1 }, 1))
    s = jobsReducer(s, { type: 'HYDRATE', seq: 2, jobs: [] })
    expect(s['14']).toBeUndefined()
  })
})

describe('activeJobs', () => {
  it('lists only live jobs, in a stable order', () => {
    let s = {}
    s = jobsReducer(s, ws({ type: 'progress', book_id: 79, percent: 1 }, 1))
    s = jobsReducer(s, ws({ type: 'progress', book_id: 14, percent: 1 }, 2))
    s = jobsReducer(s, ws({ type: 'error', book_id: 3, message: 'boom' }, 3))

    expect(activeJobs(s).map((j) => j.book_id)).toEqual([14, 79])
  })
})

describe('local start', () => {
  it('marks a book running before the first ws tick arrives', () => {
    const s = jobsReducer({}, { type: 'LOCAL_START', bookId: 14, seq: 1 })
    expect(isBookRunning(s, 14)).toBe(true)
  })
})

describe('pending prompts survive until they are answered', () => {
  // The bug: a review or chapter-conflict modal appeared off the socket and
  // vanished a moment later, leaving the run parked with no way to unblock it.
  const REVIEW = { type: 'entity_review_needed', book_id: 14, entities: { characters: {} }, context: 'x' }
  const CONFLICT = {
    type: 'chapter_conflict_needed', book_id: 14, chapter_number: 7,
    existing_untranslated: ['a'], new_untranslated: ['b'],
  }

  it('keeps the prompt when the snapshot predates it', () => {
    // The poll went out before the prompt existed, so it answers "running".
    let s = jobsReducer({}, ws(REVIEW, 5))
    s = jobsReducer(s, {
      type: 'HYDRATE', seq: 4,
      jobs: [{ book_id: 14, status: 'running', is_running: true }],
    })

    expect(s['14'].status).toBe('awaiting_review')
    expect(s['14'].pending_review).toBeTruthy()
    expect(pendingPrompts(s)).toHaveLength(1)
  })

  it('does not let a pre-start snapshot delete a book that is now parked', () => {
    // Same race, one step earlier: the snapshot was taken before the job began,
    // so the book is missing from it entirely. Deleting the entry here also
    // stopped the poll, which is what made the hang permanent.
    let s = jobsReducer({}, { type: 'LOCAL_START', bookId: 14, seq: 4 })
    s = jobsReducer(s, ws(CONFLICT, 5))
    s = jobsReducer(s, { type: 'HYDRATE', seq: 3, jobs: [] })

    expect(s['14'].status).toBe('awaiting_chapter_conflict')
    expect(s['14'].pending_chapter_conflict).toBeTruthy()
  })

  it('is not dismissed by a stray progress tick', () => {
    let s = jobsReducer({}, ws(REVIEW, 1))
    s = jobsReducer(s, ws({ type: 'progress', book_id: 14, phase: 'chunk', percent: 90 }, 2))

    expect(s['14'].status).toBe('awaiting_review')
    expect(s['14'].progress.percent).toBe(90)
  })

  it('clears on the answer, without waiting for a snapshot', () => {
    let s = jobsReducer({}, ws(REVIEW, 1))
    s = jobsReducer(s, { type: 'PROMPT_RESOLVED', bookId: 14, knownSeq: s['14'].seq, seq: 2 })

    expect(s['14'].status).toBe('running')
    expect(s['14'].pending_review).toBeNull()
    expect(pendingPrompts(s)).toHaveLength(0)
  })

  it('does not swallow the next conflict a renumber surfaces', () => {
    // The reply to the first conflict can land after the backend has already
    // asked the next question.
    let s = jobsReducer({}, ws(CONFLICT, 1))
    const answered = s['14'].seq
    s = jobsReducer(s, ws({ ...CONFLICT, chapter_number: 8 }, 2))
    s = jobsReducer(s, { type: 'PROMPT_RESOLVED', bookId: 14, knownSeq: answered, seq: 3 })

    expect(s['14'].status).toBe('awaiting_chapter_conflict')
    expect(s['14'].pending_chapter_conflict.chapter_number).toBe(8)
  })

  it('still clears a prompt on a terminal event', () => {
    let s = jobsReducer({}, ws(REVIEW, 1))
    s = jobsReducer(s, ws({ type: 'translation_cancelled', book_id: 14 }, 2))

    expect(s['14'].pending_review).toBeNull()
    expect(pendingPrompts(s)).toHaveLength(0)
  })
})
