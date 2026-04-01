import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Books from './pages/Books'
import ChapterEditor from './pages/ChapterEditor'
import Entities from './pages/Entities'
import Queue from './pages/Queue'
import Settings from './pages/Settings'
import Help from './pages/Help'
import Reader from './pages/Reader'
import Library from './pages/Library'
import BookDetail from './pages/BookDetail'
import Login from './pages/Login'
import { api } from './services/api'

// ------------------------------------------------------------------
// WebSocket context — single connection, all pages share it
// ------------------------------------------------------------------
const WsContext = createContext(null)
export const useWs = () => useContext(WsContext)

function WsProvider({ children }) {
  const [lastMessage, setLastMessage] = useState(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)
  const listenersRef = useRef(new Set())

  const subscribe = useCallback((fn) => {
    listenersRef.current.add(fn)
    return () => listenersRef.current.delete(fn)
  }, [])

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/ws`)
    wsRef.current = ws

    ws.onopen    = () => { setConnected(true); clearTimeout(reconnectTimer.current) }
    ws.onclose   = () => {
      setConnected(false)
      reconnectTimer.current = setTimeout(connect, 2000)
    }
    ws.onerror   = () => ws.close()
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        setLastMessage(msg)
        listenersRef.current.forEach(fn => fn(msg))
      } catch { /* ignore */ }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return (
    <WsContext.Provider value={{ lastMessage, connected, subscribe }}>
      {children}
    </WsContext.Provider>
  )
}

// ------------------------------------------------------------------
// Auth-gated admin routes
// ------------------------------------------------------------------
function AdminRoutes() {
  const [authChecked, setAuthChecked] = useState(false)
  const [needsLogin, setNeedsLogin] = useState(false)

  useEffect(() => {
    api.authStatus()
      .then(({ auth_required, authenticated }) => {
        setNeedsLogin(auth_required && !authenticated)
        setAuthChecked(true)
      })
      .catch(() => {
        setAuthChecked(true)
      })
  }, [])

  if (!authChecked) {
    return <div className="min-h-screen bg-slate-900" />
  }

  if (needsLogin) {
    return <Login onSuccess={() => setNeedsLogin(false)} />
  }

  return (
    <WsProvider>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="books" element={<Books />} />
          <Route path="books/:bookId/chapters/:chapterNum/edit" element={<ChapterEditor />} />
          <Route path="entities" element={<Entities />} />
          <Route path="queue" element={<Queue />} />
          <Route path="settings" element={<Settings />} />
          <Route path="help" element={<Help />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </WsProvider>
  )
}

// ------------------------------------------------------------------
// App — public routes are outside the auth gate (when enabled)
// ------------------------------------------------------------------
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public routes — gated server-side via auth middleware when public library is off */}
        <Route path="/library" element={<Library />} />
        <Route path="/library/book/:bookId" element={<BookDetail />} />
        <Route path="/library/read/:bookId/:chapterNum" element={<Reader isPublic />} />
        <Route path="/library/read/:bookId" element={<Reader isPublic />} />
        <Route path="/read/:bookId/:chapterNum" element={<Reader isPublic />} />
        <Route path="/read/:bookId" element={<Reader isPublic />} />
        {/* Admin routes — auth gated */}
        <Route path="/*" element={<AdminRoutes />} />
      </Routes>
    </BrowserRouter>
  )
}
