import { useQueryClient } from '@tanstack/react-query'
import { useWsEvent } from '../hooks/useWsEvent'

// WS event types after which server-side lists/status may have changed.
const LIST_CHANGING_EVENTS = new Set([
  'translation_complete',
  'auto_process_done',
  'translation_cancelled',
  'error',
])

/**
 * Bridges WebSocket events to react-query cache invalidation. Mounted once
 * inside the admin WsProvider tree (see AdminGate in App.jsx) so every admin
 * page's queries stay fresh without per-page manual reload chains.
 *
 * - translation lifecycle events → invalidate books/queue/job-status/chapters
 * - ws_reconnected → blanket invalidation (catch up on anything missed)
 * - progress / activity_log → no-op (too chatty; pages consume them directly)
 *
 * Replayed events (backlog the backend sends on every connect) are ignored:
 * on a first connect every query is already fetching fresh on mount, and on a
 * reconnect the ws_reconnected blanket invalidation covers the same ground.
 * Invalidating per replayed event instead fired one refetch per buffered
 * event — a fresh tab could burst dozens of /api/translate/status requests.
 */
export default function WsQueryBridge() {
  const queryClient = useQueryClient()

  useWsEvent((msg) => {
    if (msg.replayed) return

    if (LIST_CHANGING_EVENTS.has(msg.type)) {
      queryClient.invalidateQueries({ queryKey: ['books'] })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['job-status'] })
      queryClient.invalidateQueries({ queryKey: ['chapters'] })
    } else if (msg.type === 'ws_reconnected') {
      queryClient.invalidateQueries()
    }
  })

  return null
}
