/**
 * @vitest-environment jsdom
 *
 * Regression guards for a job card that never clears.
 *
 * Root cause: hydration was gated on `data !== hydratedFor.current`, a
 * reference check against React Query's cache. React Query's structural
 * sharing returns the SAME object when a refetch produces an equal payload, so
 * whenever the server's answer stopped changing the dispatch was silently
 * skipped — and with refetchOnWindowFocus off app-wide, nothing else would
 * ever correct the map for the life of the tab.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const listJobs = vi.fn()
vi.mock('../services/api', () => ({ api: { listJobs: (...a) => listJobs(...a) } }))

// Stand-in for the real WS fan-out: tests push messages through `emit`.
// Mirrors the real hook — subscribe once, dispatch through a ref — so the
// handler always runs the latest render's closure and listeners can't pile up.
let listeners = new Set()
const emit = (msg) => act(() => { [...listeners].forEach((fn) => fn(msg)) })
vi.mock('./useWsEvent', async () => {
  const { useEffect, useRef } = await import('react')
  return {
    useWsEvent: (handler) => {
      const ref = useRef(handler)
      ref.current = handler
      useEffect(() => {
        const fn = (msg) => ref.current(msg)
        listeners.add(fn)
        return () => listeners.delete(fn)
      }, [])
    },
  }
})

const { JobsProvider, useJobs } = await import('./useJobs')

let api = null
function Probe() {
  api = useJobs()
  return (
    <>
      <div data-testid="active">{api.active.map((j) => `${j.book_id}:${j.status}`).join(',') || 'none'}</div>
      <div data-testid="prompts">{api.prompts.map((j) => `${j.book_id}:${j.status}`).join(',') || 'none'}</div>
    </>
  )
}
const shown = () => screen.getByTestId('active').textContent
const prompted = () => screen.getByTestId('prompts').textContent

const IDLE = { jobs: [], running: 0, max_concurrent: 3 }
const RUNNING_78 = {
  jobs: [{ book_id: 78, status: 'running', is_running: true, chapter_number: 1095 }],
  running: 1,
  max_concurrent: 3,
}

// Resolve like a real request would: off the current tick, and far enough
// apart that two fetches never share a Date.now() millisecond.
const reply = (payload) => () => new Promise((r) => setTimeout(() => r({ ...payload }), 5))
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 40)) })

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <JobsProvider><Probe /></JobsProvider>
    </QueryClientProvider>
  )
  return qc
}

describe('useJobs', () => {
  beforeEach(() => {
    listeners = new Set()
    api = null
    listJobs.mockReset()
  })

  afterEach(() => cleanup())

  it('drops an optimistically-started job the server never reports', async () => {
    // The server is idle throughout and keeps saying so — a fresh but equal
    // object each call, exactly as HTTP+JSON produces. This is the shape that
    // used to make hydration a no-op forever.
    listJobs.mockImplementation(reply(IDLE))
    mount()
    await flush()

    // "Translate" clicked: the book is marked running before the first tick.
    // If the run never materialises (POST rejected, or it ends immediately),
    // only hydration can take the card back down.
    act(() => api.markStarted(78))
    expect(shown()).toBe('78:running')

    act(() => { api.refresh() })
    await flush()

    expect(shown()).toBe('none')
  })

  it('clears a live job on a replayed terminal event, without waiting for a refetch', async () => {
    // The backend broadcasts the terminal events before it releases the job
    // slot, so a refetch racing them still answers "running". If the socket was
    // down when they fired, the tab only ever sees them flagged `replayed` —
    // dropping those is what left the card up.
    let snapshot = RUNNING_78
    listJobs.mockImplementation(() => reply(snapshot)())
    mount()
    await flush()
    expect(shown()).toBe('78:running')

    // Delivered on reconnect, while the server would still report it running.
    emit({ type: 'translation_complete', book_id: 78, chapter: 1095, replayed: true })
    expect(shown()).toBe('none')

    // And it stays down once the server has caught up.
    snapshot = IDLE
    emit({ type: 'ws_reconnected' })
    await flush()
    expect(shown()).toBe('none')
  })

  it('still ignores a replayed terminal for a book it is not tracking', async () => {
    // A fresh tab replaying a backlog of old completions must not sprout cards.
    listJobs.mockImplementation(reply(IDLE))
    mount()
    await flush()

    emit({ type: 'translation_complete', book_id: 78, chapter: 1095, replayed: true })
    await flush()

    expect(shown()).toBe('none')
    expect(api.jobFor(78)).toBeNull()
  })
})

describe('useJobs — prompts vs. in-flight snapshots', () => {
  beforeEach(() => {
    listeners = new Set()
    api = null
    listJobs.mockReset()
  })

  afterEach(() => cleanup())

  it('keeps a prompt that arrived while a snapshot was in flight', async () => {
    // The failure the user saw: the modal opened off the socket and vanished a
    // moment later, because the answer to a request issued BEFORE the prompt
    // existed was treated as the newer truth. The run then sat parked with
    // nothing on screen to unblock it.
    listJobs.mockImplementation(reply(RUNNING_78))
    mount()
    await flush()
    expect(shown()).toBe('78:running')

    // A poll goes out (server still mid-chapter)...
    let release = null
    listJobs.mockImplementation(() => new Promise((r) => { release = () => r({ ...RUNNING_78 }) }))
    act(() => { api.refresh() })
    await act(async () => { await new Promise((r) => setTimeout(r, 5)) })

    // ...the chapter conflict fires while it is still in flight...
    emit({
      type: 'chapter_conflict_needed', book_id: 78, chapter_number: 1095,
      existing_untranslated: ['a'], new_untranslated: ['b'],
    })
    expect(prompted()).toBe('78:awaiting_chapter_conflict')

    // ...and the stale "running" answer lands afterwards.
    await act(async () => { release(); await new Promise((r) => setTimeout(r, 10)) })

    expect(prompted()).toBe('78:awaiting_chapter_conflict')
    expect(api.jobFor(78).pending_chapter_conflict).toBeTruthy()
  })

  it('closes the modal on the answer, before the next snapshot', async () => {
    listJobs.mockImplementation(reply(RUNNING_78))
    mount()
    await flush()

    emit({ type: 'entity_review_needed', book_id: 78, entities: {}, context: 'x' })
    expect(prompted()).toBe('78:awaiting_review')

    act(() => api.resolvePrompt(78, api.jobFor(78).seq))
    expect(prompted()).toBe('none')
    expect(shown()).toBe('78:running')
  })
})

describe('useJobs — replayed backlog vs. an open prompt', () => {
  // A parked job is the one state where the backlog is provably wrong: the
  // worker thread is blocked on the answer, so an earlier chapter's
  // `translation_complete` is stale news. Every socket reconnect replays it,
  // and on a flaky link that is every few seconds.
  const PARKED_78 = {
    jobs: [{
      book_id: 78, status: 'awaiting_chapter_conflict', is_running: true,
      pending_chapter_conflict: { book_id: 78, chapter_number: 1096 },
    }],
    running: 1,
    max_concurrent: 3,
  }

  beforeEach(() => {
    listeners = new Set()
    api = null
    listJobs.mockReset()
  })

  afterEach(() => cleanup())

  it('ignores a replayed completion while the book is parked on a prompt', async () => {
    listJobs.mockImplementation(reply(PARKED_78))
    mount()
    await flush()
    expect(prompted()).toBe('78:awaiting_chapter_conflict')

    emit({ type: 'translation_complete', book_id: 78, chapter: 1095, seq: 41, replayed: true })

    expect(prompted()).toBe('78:awaiting_chapter_conflict')
    expect(api.jobFor(78).pending_chapter_conflict).toBeTruthy()
  })

  it('ignores a replayed event this tab already saw live', async () => {
    listJobs.mockImplementation(reply(RUNNING_78))
    mount()
    await flush()

    // Seen live: the run is over and the card clears.
    emit({ type: 'translation_complete', book_id: 78, chapter: 1095, seq: 41 })
    expect(shown()).toBe('none')

    // The user starts the next chapter; the reconnect replays the old copy.
    act(() => api.markStarted(78))
    expect(shown()).toBe('78:running')
    emit({ type: 'translation_complete', book_id: 78, chapter: 1095, seq: 41, replayed: true })
    expect(shown()).toBe('78:running')
  })

  it('still adopts a terminal event it genuinely missed', async () => {
    listJobs.mockImplementation(reply(RUNNING_78))
    mount()
    await flush()
    expect(shown()).toBe('78:running')

    emit({ type: 'translation_complete', book_id: 78, chapter: 1095, seq: 41, replayed: true })
    expect(shown()).toBe('none')
  })
})
