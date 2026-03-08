/**
 * useAutoProcess — app-level hook that auto-starts the next queue item
 * after a translation completes. Reads settings from localStorage so it
 * works regardless of which page is currently mounted.
 */
import { useEffect, useRef } from 'react'
import { useWs } from '../App'
import { api } from '../services/api'

function ls(key, fallback) {
  try {
    const v = localStorage.getItem(key)
    return v !== null ? JSON.parse(v) : fallback
  } catch {
    return fallback
  }
}

export function useAutoProcess() {
  const { lastMessage } = useWs()
  // Guard against double-firing for the same message
  const lastHandled = useRef(null)

  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'translation_complete') return
    // Deduplicate — WS context may re-render multiple consumers
    const msgId = lastMessage.title + ':' + lastMessage.chapter
    if (lastHandled.current === msgId) return
    lastHandled.current = msgId

    const autoProcess = ls('queue.autoProcess', false)
    if (!autoProcess) return

    // Kick off the next item after a short pause
    setTimeout(async () => {
      const filterBook = ls('queue.filterBook', '')
      const bookId = filterBook ? parseInt(filterBook) : null
      try {
        const d = await api.listQueue(bookId || undefined)
        if ((d.count || 0) === 0) return
        await api.processNext({
          book_id: bookId,
          translation_model: ls('queue.translationModel', '') || null,
          advice_model: ls('shared.adviceModel', '') || null,
          cleaning_model: ls('shared.cleaningModel', '') || null,
          no_review: ls('queue.noReview', false),
          no_clean: ls('queue.noClean', false),
          no_repair: ls('queue.noRepair', false),
        })
      } catch {
        // Silently fail — the Queue page will show the error when visited
      }
    }, 800)
  }, [lastMessage])
}
