import { useState, useCallback, useRef, useMemo } from 'react'
import { api } from '../services/api'

/**
 * Compute matches within a single chapter's text (client-side).
 * Returns array of { line, col, length, field }.
 */
function computeMatches(query, translatedText, untranslatedLines, scope, isRegex) {
  if (!query) return []
  const results = []

  let re
  if (isRegex) {
    try { re = new RegExp(query, 'gi') }
    catch { return [] }
  }

  if (scope === 'untranslated' || scope === 'both') {
    for (let i = 0; i < untranslatedLines.length; i++) {
      const ln = untranslatedLines[i]
      if (isRegex) {
        re.lastIndex = 0
        let m
        while ((m = re.exec(ln)) !== null) {
          results.push({ line: i, col: m.index, length: m[0].length, field: 'untranslated' })
          if (m[0].length === 0) re.lastIndex++
        }
      } else {
        const lnLower = ln.toLowerCase()
        const qLower = query.toLowerCase()
        let pos = 0
        while (true) {
          const idx = lnLower.indexOf(qLower, pos)
          if (idx === -1) break
          results.push({ line: i, col: idx, length: query.length, field: 'untranslated' })
          pos = idx + 1
        }
      }
    }
  }

  if (scope === 'translated' || scope === 'both') {
    const transLines = translatedText.split('\n')
    for (let i = 0; i < transLines.length; i++) {
      const ln = transLines[i]
      if (isRegex) {
        re.lastIndex = 0
        let m
        while ((m = re.exec(ln)) !== null) {
          results.push({ line: i, col: m.index, length: m[0].length, field: 'translated' })
          if (m[0].length === 0) re.lastIndex++
        }
      } else {
        const lnLower = ln.toLowerCase()
        const qLower = query.toLowerCase()
        let pos = 0
        while (true) {
          const idx = lnLower.indexOf(qLower, pos)
          if (idx === -1) break
          results.push({ line: i, col: idx, length: query.length, field: 'translated' })
          pos = idx + 1
        }
      }
    }
  }

  return results
}

// Navigate in a flat match list, return new index
function advanceIndex(current, length, delta) {
  if (length === 0) return 0
  return (current + delta + length) % length
}

export function useSearch() {
  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [scope, setScope] = useState('translated')
  const [isRegex, setIsRegex] = useState(false)
  const [isBookWide, setIsBookWide] = useState(false)

  const [chapterMatches, setChapterMatches] = useState([])
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0)

  const [bookResults, setBookResults] = useState(null)
  const [bookMatchOrder, setBookMatchOrder] = useState([])
  const [bookCurrentIndex, setBookCurrentIndex] = useState(0)
  const [bookSearchLoading, setBookSearchLoading] = useState(false)

  const searchInputRef = useRef(null)
  const replaceInputRef = useRef(null)

  // Refs for synchronous access in callbacks (avoid stale closures)
  const bookMatchOrderRef = useRef(bookMatchOrder)
  bookMatchOrderRef.current = bookMatchOrder
  const bookCurrentIndexRef = useRef(0)
  bookCurrentIndexRef.current = bookCurrentIndex

  const totalMatches = isBookWide
    ? (bookMatchOrder.length > 0 ? bookMatchOrder.length : chapterMatches.length)
    : chapterMatches.length

  const currentIndex = isBookWide
    ? (bookMatchOrder.length > 0 ? bookCurrentIndex : currentMatchIndex)
    : currentMatchIndex

  const activeMatch = useMemo(() => {
    if (isBookWide) {
      if (bookMatchOrder.length > 0) {
        return bookMatchOrder[bookCurrentIndex] || null
      }
      if (chapterMatches.length === 0) return null
      return chapterMatches[currentMatchIndex] || null
    }
    if (chapterMatches.length === 0) return null
    return chapterMatches[currentMatchIndex] || null
  }, [isBookWide, bookMatchOrder, bookCurrentIndex, chapterMatches, currentMatchIndex])

  const updateChapterMatches = useCallback(function doUpdate(q, translatedText, untranslatedLines, sc, re) {
    const found = computeMatches(q, translatedText, untranslatedLines, sc, re)
    setChapterMatches(found)
    setCurrentMatchIndex(function clamp(cur) {
      return found.length > 0 ? Math.min(cur, found.length - 1) : 0
    })
  }, [])

  const searchBook = useCallback(async function doSearch(bookId, q, sc, re) {
    if (!q) {
      setBookResults(null)
      setBookMatchOrder([])
      setBookCurrentIndex(0)
      return
    }
    setBookSearchLoading(true)
    try {
      const res = await api.searchBook(parseInt(bookId), {
        query: q, scope: sc, is_regex: re
      })
      setBookResults(res)
      const flat = []
      for (const ch of (res.results || [])) {
        for (let i = 0; i < ch.matches.length; i++) {
          flat.push({ chapterNum: ch.chapter_number, matchIdx: i, ...ch.matches[i] })
        }
      }
      setBookMatchOrder(flat)
      bookMatchOrderRef.current = flat
      setBookCurrentIndex(0)
      bookCurrentIndexRef.current = 0
    } catch (err) {
      console.error('Book search error:', err)
      setBookResults(null)
      setBookMatchOrder([])
      bookMatchOrderRef.current = []
    } finally {
      setBookSearchLoading(false)
    }
  }, [])

  const nextMatch = useCallback(function goNext(currentChapterNum) {
    if (!isBookWide) {
      if (chapterMatches.length === 0) return null
      setCurrentMatchIndex(function inc(c) { return (c + 1) % chapterMatches.length })
      return null
    }
    var matchList = bookMatchOrderRef.current
    if (matchList.length === 0) {
      // Book search still loading — use chapter matches
      if (chapterMatches.length > 0) {
        setCurrentMatchIndex(function inc(c) { return (c + 1) % chapterMatches.length })
      }
      return null
    }
    var nextIdx = advanceIndex(bookCurrentIndexRef.current, matchList.length, 1)
    setBookCurrentIndex(nextIdx)
    bookCurrentIndexRef.current = nextIdx
    var target = matchList[nextIdx]
    if (target && target.chapterNum !== currentChapterNum) {
      return { navigateTo: target.chapterNum }
    }
    return null
  }, [isBookWide, chapterMatches])

  const prevMatch = useCallback(function goPrev(currentChapterNum) {
    if (!isBookWide) {
      if (chapterMatches.length === 0) return null
      setCurrentMatchIndex(function dec(c) { return (c - 1 + chapterMatches.length) % chapterMatches.length })
      return null
    }
    var matchList = bookMatchOrderRef.current
    if (matchList.length === 0) {
      if (chapterMatches.length > 0) {
        setCurrentMatchIndex(function dec(c) { return (c - 1 + chapterMatches.length) % chapterMatches.length })
      }
      return null
    }
    var prevIdx = advanceIndex(bookCurrentIndexRef.current, matchList.length, -1)
    setBookCurrentIndex(prevIdx)
    bookCurrentIndexRef.current = prevIdx
    var target = matchList[prevIdx]
    if (target && target.chapterNum !== currentChapterNum) {
      return { navigateTo: target.chapterNum }
    }
    return null
  }, [isBookWide, chapterMatches])

  const replaceCurrentMatch = useCallback(function doReplace(txt, matchObj) {
    if (!matchObj || matchObj.field !== 'translated') return txt
    var splitLines = txt.split('\n')
    if (matchObj.line >= splitLines.length) return txt
    var ln = splitLines[matchObj.line]
    var matched = ln.substring(matchObj.col, matchObj.col + matchObj.length)
    var replacement = replaceText
    if (isRegex) {
      try {
        var re = new RegExp(query, 'gi')
        replacement = matched.replace(new RegExp(query, 'i'), replaceText)
      } catch { /* fall back to literal replaceText */ }
    }
    splitLines[matchObj.line] = ln.substring(0, matchObj.col) + replacement + ln.substring(matchObj.col + matchObj.length)
    return splitLines.join('\n')
  }, [replaceText, isRegex, query])

  const replaceAllInChapter = useCallback(function doReplaceAll(txt) {
    if (!query) return txt
    var splitLines = txt.split('\n')
    var newLines = splitLines.map(function processLine(ln) {
      if (isRegex) {
        try { return ln.replace(new RegExp(query, 'gi'), replaceText) }
        catch { return ln }
      }
      var lnLower = ln.toLowerCase()
      var qLower = query.toLowerCase()
      var out = ''
      var pos = 0
      while (true) {
        var idx = lnLower.indexOf(qLower, pos)
        if (idx === -1) { out += ln.substring(pos); break }
        out += ln.substring(pos, idx) + replaceText
        pos = idx + query.length
      }
      return out
    })
    return newLines.join('\n')
  }, [query, replaceText, isRegex])

  const open = useCallback(function doOpen(opts) {
    var focusReplace = opts && opts.focusReplace
    setIsOpen(true)
    setTimeout(function focusInput() {
      if (focusReplace && replaceInputRef.current) {
        replaceInputRef.current.focus()
      } else if (searchInputRef.current) {
        searchInputRef.current.select()
        searchInputRef.current.focus()
      }
    }, 50)
  }, [])

  const close = useCallback(function doClose() {
    setIsOpen(false)
    setQuery('')
    setReplaceText('')
    setChapterMatches([])
    setCurrentMatchIndex(0)
    setBookResults(null)
    setBookMatchOrder([])
    bookMatchOrderRef.current = []
    setBookCurrentIndex(0)
    bookCurrentIndexRef.current = 0
  }, [])

  const syncBookIndexToChapter = useCallback(function doSync(chapterNum) {
    var list = bookMatchOrderRef.current
    var idx = list.findIndex(function findCh(m) { return m.chapterNum === chapterNum })
    if (idx >= 0) {
      setBookCurrentIndex(idx)
      bookCurrentIndexRef.current = idx
    }
  }, [])

  return {
    isOpen, query, setQuery, replaceText, setReplaceText,
    scope, setScope, isRegex, setIsRegex,
    isBookWide, setIsBookWide,
    chapterMatches, currentMatchIndex, setCurrentMatchIndex,
    bookResults, bookMatchOrder, bookCurrentIndex, setBookCurrentIndex,
    bookSearchLoading,
    totalMatches, currentIndex, activeMatch,
    updateChapterMatches, searchBook,
    nextMatch, prevMatch,
    replaceCurrentMatch, replaceAllInChapter,
    open, close,
    searchInputRef, replaceInputRef,
    syncBookIndexToChapter,
  }
}
