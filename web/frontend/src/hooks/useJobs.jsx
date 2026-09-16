import { createContext, useCallback, useContext, useMemo, useReducer, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../services/api'
import { useWsEvent } from './useWsEvent'
import {
  activeJobs, isActiveStatus, isBookRunning, jobKey, jobKeyOf, jobsReducer, needsAttention,
  pendingPrompts,
} from '../lib/jobs'

/**
 * One shared view of every running translation.
 *
 * Dashboard and Queue used to keep separate copies of a single global status
 * and each ran their own WebSocket handler — which is exactly the bug class
 * that made two pages disagree about whether anything was running. They now
 * both read this.
 */
const JobsContext = createContext(null)

// Terminal events end a run. Applying one can only ever clear a job, never
// start one, which is what makes it safe to act on a replayed copy (below).
const TERMINAL_TYPES = ['translation_complete', 'translation_cancelled', 'error']

// While the UI believes something is running, poll as a safety net. The socket
// is the primary channel and this is deliberately slow, but a terminal event
// delivered while the socket was down is invisible to this tab (the backend
// replays it flagged, and a fresh tab must not act on that), and an
// invalidation can coalesce with an in-flight fetch that predates the job
// being released. Without a poll either case strands a card forever: nothing
// else refetches, because refetchOnWindowFocus is off app-wide.
const ACTIVE_POLL_MS = 10_000

export function JobsProvider({ children }) {
  const [jobs, dispatch] = useReducer(jobsReducer, {})
  const queryClient = useQueryClient()

  // Local monotonic clock. Lets the reducer ignore a hydration response that
  // raced a newer socket message, instead of the one-shot "restore once" hack
  // the Dashboard used to need.
  const seqRef = useRef(0)
  const nextSeq = () => ++seqRef.current

  const hasLocalActive = useMemo(
    () => Object.values(jobs).some((job) => isActiveStatus(job.status)), [jobs])

  const { data, dataUpdatedAt } = useQuery({
    queryKey: ['jobs'],
    // Stamp the sequence when the request GOES OUT, not when it lands. The
    // snapshot describes the server as it was at that moment, so anything the
    // socket delivers while the request is in flight is strictly newer — and
    // must survive the hydration. Stamping on arrival made every snapshot look
    // newest, which is how a review or chapter-conflict modal could open off a
    // socket message and be erased milliseconds later by an older answer,
    // leaving the run parked with nothing on screen to unblock it.
    queryFn: async () => {
      const seq = nextSeq()
      const payload = await api.listJobs()
      return { ...payload, seq }
    },
    staleTime: 5000,
    // Keep polling while EITHER side thinks work is live. Trusting only the
    // local map meant one bad hydration (dropping the last job) also switched
    // the poll off, and with refetchOnWindowFocus off app-wide nothing would
    // ever ask again.
    refetchInterval: (query) =>
      (hasLocalActive || (query.state.data?.running ?? 0) > 0 ? ACTIVE_POLL_MS : false),
  })

  // Hydrate on every successful fetch, not every time `data` changes identity.
  // React Query's structural sharing returns the SAME object when a refetch
  // produces an equal payload, so an identity check alone silently skipped the
  // dispatch in exactly the case that needs it most: the server has gone idle
  // and keeps saying so, while this tab still holds a job the socket never got
  // to close. `dataUpdatedAt` moves on every fetch, identical payload or not;
  // the identity half then covers two fetches landing in the same millisecond.
  const hydratedAt = useRef(0)
  const hydratedData = useRef(null)
  if (data && (dataUpdatedAt !== hydratedAt.current || data !== hydratedData.current)) {
    hydratedAt.current = dataUpdatedAt
    hydratedData.current = data
    dispatch({ type: 'HYDRATE', jobs: data.jobs || [], seq: data.seq ?? nextSeq() })
  }

  // Highest server-stamped `seq` this tab has seen. Every buffered (therefore
  // replayable) event carries one, and the live copy carries the SAME one, so
  // this is how a replayed event we already acted on is told apart from one we
  // missed while the socket was down.
  const lastServerSeq = useRef(0)

  useWsEvent(useCallback((msg) => {
    if (msg.type === 'ws_reconnected') {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      return
    }
    if (typeof msg.seq === 'number') {
      // Old news: this tab was connected when it first went out. The socket
      // reconnects constantly on a flaky link, and every reconnect replays the
      // whole backlog — including the completion of an EARLIER chapter of a
      // run that is still going.
      if (msg.replayed && msg.seq <= lastServerSeq.current) return
      lastServerSeq.current = Math.max(lastServerSeq.current, msg.seq)
    }
    if (msg.replayed) {
      // A job parked on a prompt has not ended, whatever the backlog says: the
      // worker thread is blocked on the answer. Letting a replayed terminal
      // event through here cleared the payload the modal renders from, leaving
      // the run unanswerable until the next 10s snapshot put it back.
      const known = jobs[jobKeyOf(msg) ?? '']
      if (needsAttention(known)) return
      // The rest of the backlog describes runs that already ended. Adopting it
      // wholesale would resurrect a finished card on every fresh tab — but a
      // terminal event for a book this tab still shows as active is the
      // missing end of a run it watched start, and dropping that stranded the
      // card.
      if (!(TERMINAL_TYPES.includes(msg.type) && isActiveStatus(known?.status))) return
    }
    dispatch({ type: 'WS', msg, seq: nextSeq() })
  }, [queryClient, jobs]))

  const value = useMemo(() => {
    const active = activeJobs(jobs)
    const maxConcurrent = data?.max_concurrent ?? 3
    return {
      jobs,
      active,
      prompts: pendingPrompts(jobs),
      maxConcurrent,
      atCapacity: active.length >= maxConcurrent,
      anyRunning: active.length > 0,
      isBookRunning: (bookId) => isBookRunning(jobs, bookId),
      jobFor: (bookId) => jobs[jobKey(bookId)] || null,
      // Optimistic: mark the book running the moment we POST, so the button
      // state doesn't flicker while the first progress tick is in flight.
      markStarted: (bookId) => dispatch({ type: 'LOCAL_START', bookId, seq: nextSeq() }),
      refresh: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
      // Called the moment a prompt is answered, so the modal closes on the
      // answer itself rather than on whichever snapshot happens to win next.
      resolvePrompt: (bookId, knownSeq) =>
        dispatch({ type: 'PROMPT_RESOLVED', bookId, knownSeq, seq: nextSeq() }),
    }
  }, [jobs, data, queryClient])

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>
}

export function useJobs() {
  const ctx = useContext(JobsContext)
  if (!ctx) throw new Error('useJobs must be used inside <JobsProvider>')
  return ctx
}
