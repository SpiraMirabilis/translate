import { useEffect, useRef } from 'react'
import { useWs } from '../App'

/**
 * Subscribe to every WebSocket message via the WsProvider fan-out.
 *
 * Unlike the old `lastMessage` context state (which React 18 batching could
 * collapse, dropping messages that arrived in the same frame), subscribe()
 * delivers each message synchronously to the handler. The handler is kept in
 * a ref so the latest render's closure always runs without re-subscribing.
 *
 * The handler also receives a synthetic `{ type: 'ws_reconnected' }` event
 * when the socket re-opens after a drop — use it for one-shot catch-up.
 * Note the backend replays missed events on connect (flagged `replayed: true`
 * with a `seq`); it does NOT replay `activity_log` or `progress` messages.
 */
export function useWsEvent(handler) {
  const { subscribe } = useWs()
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => subscribe((msg) => ref.current(msg)), [subscribe])
}
