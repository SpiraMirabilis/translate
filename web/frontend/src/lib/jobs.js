/**
 * Client-side model for concurrent per-book translation jobs.
 *
 * Several books can translate at once, so the UI keeps a map of jobs keyed by
 * book rather than one global "is something running" scalar. Kept pure and
 * dependency-free so it can be unit tested without React.
 */

// The Dashboard can translate pasted text with no book selected
// (`book_id: null`), which still needs a slot in the map.
export const NO_BOOK_KEY = '__nobook__'

/** Map key for a book id (or the bookless job). */
export function jobKey(bookId) {
  return bookId == null ? NO_BOOK_KEY : String(bookId)
}

/** Which job a WebSocket message belongs to, or null if it isn't job-scoped. */
export function jobKeyOf(msg) {
  if (!msg) return null
  if ('book_id' in msg) return jobKey(msg.book_id)
  return null
}

/** Statuses that mean a job still holds a slot. */
export const ACTIVE_STATUSES = [
  'running',
  'waiting',
  'awaiting_review',
  'awaiting_json_fix',
  'awaiting_chapter_conflict',
]

export function isActiveStatus(status) {
  return ACTIVE_STATUSES.includes(status)
}

/** True when a job is blocked waiting for a human. */
export function needsAttention(job) {
  return !!job && typeof job.status === 'string' && job.status.startsWith('awaiting_')
}

const TERMINAL_BY_TYPE = {
  translation_complete: 'complete',
  translation_cancelled: 'idle',
  error: 'error',
}

/**
 * Fold one event into the jobs map.
 *
 * Every entry is replaced rather than mutated so React sees a new object.
 * `seq` is a local monotonic counter used to ignore a HYDRATE response that
 * raced a newer WebSocket message — the bug the old one-shot `restoredRef`
 * hack in Dashboard was working around.
 */
export function jobsReducer(state, action) {
  switch (action.type) {
    case 'HYDRATE': {
      const next = {}
      for (const job of action.jobs || []) {
        const key = jobKey(job.book_id)
        const prev = state[key]
        // A snapshot carries no progress ticks, and may be older than what the
        // socket already told us — `action.seq` is stamped when the request is
        // ISSUED, so anything the socket delivered while it was in flight
        // outranks it. Keep whichever the local state knows better; replacing
        // wholesale here is what dropped a pending_* payload (and with it the
        // open modal) on a snapshot taken before the prompt existed.
        if (prev && prev.seq > (action.seq || 0)) {
          next[key] = prev
          continue
        }
        next[key] = { ...job, progress: prev?.progress ?? null, seq: prev?.seq ?? 0 }
      }
      // Jobs the server no longer reports have finished; drop them unless a
      // newer local event is still describing them.
      for (const [key, prev] of Object.entries(state)) {
        if (!next[key] && prev.seq > (action.seq || 0)) next[key] = prev
      }
      return next
    }

    case 'LOCAL_START': {
      const key = jobKey(action.bookId)
      return {
        ...state,
        [key]: {
          ...(state[key] || {}),
          book_id: action.bookId ?? null,
          status: 'running',
          is_running: true,
          error: null,
          progress: null,
          seq: action.seq,
        },
      }
    }

    case 'WS': {
      const { msg, seq } = action
      const key = jobKeyOf(msg)
      if (key == null) return state
      const prev = state[key] || { book_id: msg.book_id ?? null }
      const next = { ...prev, book_id: msg.book_id ?? prev.book_id ?? null, seq }

      switch (msg.type) {
        case 'progress':
          next.progress = msg
          // A progress tick must never dismiss a prompt the user has not
          // answered: the modal is the only way to unblock that run, and the
          // worker can still emit ticks for work it finished before parking.
          // Prompts end via their own resolution, hydration or a terminal event.
          if (!needsAttention(prev)) {
            next.status = msg.phase === 'session_limit' || msg.phase === 'overloaded'
              ? 'waiting' : 'running'
          }
          next.is_running = true
          break
        case 'entity_review_needed':
          next.status = 'awaiting_review'
          next.pending_review = {
            entities: msg.entities,
            context: msg.context,
            phase: msg.phase || 'post',
            gendered_categories: msg.gendered_categories,
            note_updates: msg.note_updates || [],
          }
          break
        case 'json_fix_needed':
          next.status = 'awaiting_json_fix'
          next.pending_json_fix = { ...msg }
          break
        case 'json_fix_resolved':
          next.status = 'running'
          next.pending_json_fix = null
          break
        case 'chapter_conflict_needed':
          next.status = 'awaiting_chapter_conflict'
          next.pending_chapter_conflict = { ...msg }
          break
        case 'auto_process_stopping':
          next.auto_process_stopping = true
          break
        case 'auto_process_done':
          next.auto_process = false
          break
        case 'translation_complete':
        case 'translation_cancelled':
        case 'error':
          next.status = TERMINAL_BY_TYPE[msg.type]
          next.is_running = false
          next.progress = null
          next.pending_review = null
          next.pending_json_fix = null
          next.pending_chapter_conflict = null
          if (msg.type === 'error') next.error = msg.message || 'Translation failed.'
          if (msg.type === 'translation_complete') next.last_chapter = msg.chapter
          break
        default:
          return state
      }
      return { ...state, [key]: next }
    }

    // The user answered a prompt (or skipped it). Clear it locally rather than
    // waiting for the next snapshot: the answer travels by REST, so nothing on
    // the socket says it happened, and a hydration that loses the seq race to a
    // progress tick would leave a dead modal on screen.
    case 'PROMPT_RESOLVED': {
      const key = jobKey(action.bookId)
      const prev = state[key]
      if (!prev) return state
      // Only clear the prompt that was actually answered. Resolving a chapter
      // conflict by renumbering can surface the NEXT conflict before the reply
      // to the first one lands, and that new payload must not be swallowed —
      // `knownSeq` is the entry as it was when the modal rendered.
      if (action.knownSeq != null && prev.seq !== action.knownSeq) return state
      return {
        ...state,
        [key]: {
          ...prev,
          status: needsAttention(prev) ? 'running' : prev.status,
          pending_review: null,
          pending_json_fix: null,
          pending_chapter_conflict: null,
          seq: action.seq,
        },
      }
    }

    case 'REMOVE': {
      const next = { ...state }
      delete next[jobKey(action.bookId)]
      return next
    }

    default:
      return state
  }
}

/** Jobs that still hold a slot, in a stable order for rendering. */
export function activeJobs(state) {
  return Object.values(state)
    .filter((job) => isActiveStatus(job.status))
    .sort((a, b) => jobKey(a.book_id).localeCompare(jobKey(b.book_id)))
}

/** Every job blocked on a human decision, oldest-keyed first. */
export function pendingPrompts(state) {
  return activeJobs(state).filter(needsAttention)
}

export function isBookRunning(state, bookId) {
  return isActiveStatus(state[jobKey(bookId)]?.status)
}
