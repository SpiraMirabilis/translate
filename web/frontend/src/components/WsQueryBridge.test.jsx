import { describe, it, expect, vi, beforeEach } from 'vitest'

// Capture the handler WsQueryBridge subscribes with, so tests can deliver
// messages to it without standing up a WebSocket or a React tree.
let handler = null
vi.mock('../hooks/useWsEvent', () => ({
  useWsEvent: (fn) => { handler = fn },
}))

const invalidateQueries = vi.fn()
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries }),
}))

const { default: WsQueryBridge } = await import('./WsQueryBridge')

const keysInvalidated = () =>
  invalidateQueries.mock.calls.map(([arg]) => arg?.queryKey?.[0] ?? '*')

describe('WsQueryBridge', () => {
  beforeEach(() => {
    invalidateQueries.mockClear()
    WsQueryBridge()
  })

  it('invalidates lists on a live translation lifecycle event', () => {
    handler({ type: 'translation_complete', chapter: 7 })
    expect(keysInvalidated()).toEqual(['books', 'jobs', 'queue', 'chapters'])
  })

  it('scopes queue and chapter refetches to the book the event names', () => {
    // With several books translating these events arrive N times as often;
    // invalidating every book's lists on each one is a refetch storm.
    handler({ type: 'translation_complete', chapter: 7, book_id: 14 })

    const calls = invalidateQueries.mock.calls.map(([arg]) => arg)
    expect(calls[0].queryKey).toEqual(['books'])
    expect(calls[1].queryKey).toEqual(['jobs'])
    expect(calls[2].queryKey).toEqual(['chapters', 14])

    // The queue is keyed ['queue', filterBook || null]; book 14's view and the
    // unfiltered "All books" view refresh, book 79's does not.
    const { predicate } = calls[3]
    expect(predicate({ queryKey: ['queue', 14] })).toBe(true)
    expect(predicate({ queryKey: ['queue', null] })).toBe(true)
    expect(predicate({ queryKey: ['queue', 79] })).toBe(false)
    expect(predicate({ queryKey: ['books'] })).toBe(false)
  })

  it('ignores replayed events — the connect backlog must not refetch', () => {
    // What a fresh tab used to receive: the backend replays its buffer on every
    // connect. Each event triggered a job-status refetch, bursting one
    // /api/translate/status request per buffered event.
    for (let i = 0; i < 40; i++) {
      handler({ type: 'translation_complete', chapter: i, replayed: true, seq: i })
    }
    handler({ type: 'error', msg: 'boom', replayed: true, seq: 99 })
    expect(invalidateQueries).not.toHaveBeenCalled()
  })

  it('still blanket-invalidates on reconnect, so replayed events lose nothing', () => {
    handler({ type: 'ws_reconnected' })
    expect(keysInvalidated()).toEqual(['*'])
  })

  it('does not invalidate on chatty progress/activity events', () => {
    handler({ type: 'progress', phase: 'chunk' })
    handler({ type: 'activity_log', entry: {} })
    expect(invalidateQueries).not.toHaveBeenCalled()
  })
})
