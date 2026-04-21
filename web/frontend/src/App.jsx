import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Books from './pages/Books'
import ChapterEditor from './pages/ChapterEditor'
import Entities from './pages/Entities'
import Queue from './pages/Queue'
import Settings from './pages/Settings'
import Help from './pages/Help'
import Recommendations from './pages/Recommendations'
import ApiCalls from './pages/ApiCalls'
import ApiLogPage from './pages/ApiLogPage'
import Reader from './pages/Reader'
import Library from './pages/Library'
import BookDetail from './pages/BookDetail'
import Login from './pages/Login'
import NotFound from './pages/NotFound'
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
// Auth gate — pathless layout route that guards all admin routes
// ------------------------------------------------------------------
function AdminGate({ authState, onLoginSuccess }) {
  const needsLogin = authState.auth_required && !authState.authenticated
  if (needsLogin) {
    return <Login onSuccess={onLoginSuccess} />
  }
  return (
    <WsProvider>
      <Outlet />
    </WsProvider>
  )
}

// Catch-all for unknown top-level URIs: send unauthenticated users to the
// public library (when enabled) instead of exposing the admin shell; show a
// 404 otherwise.
function UnknownRoute({ authState }) {
  const needsLogin = authState.auth_required && !authState.authenticated
  if (needsLogin && authState.public_library) {
    return <Navigate to="/library" replace />
  }
  return <NotFound publicLibrary={authState.public_library} />
}

// ------------------------------------------------------------------
// App — public routes are outside the auth gate (when enabled)
// ------------------------------------------------------------------
export default function App() {
  const [authState, setAuthState] = useState(null)

  useEffect(() => {
    api.authStatus()
      .then(setAuthState)
      .catch(() => setAuthState({ auth_required: false, authenticated: true, public_library: true }))
  }, [])

  if (!authState) {
    return <div className="min-h-screen bg-slate-900" />
  }

  const handleLoginSuccess = () => setAuthState({ ...authState, authenticated: true })

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
        {/* Admin routes — auth gated. Only specific paths are listed, so
            unknown URIs fall through to the catch-all below rather than
            resolving to the Dashboard. */}
        <Route element={<AdminGate authState={authState} onLoginSuccess={handleLoginSuccess} />}>
          <Route path="/" element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="books" element={<Books />} />
            <Route path="books/:bookId" element={<Books />} />
            <Route path="books/:bookId/chapters/:chapterNum/edit" element={<ChapterEditor />} />
            <Route path="books/:bookId/api-calls" element={<ApiCalls />} />
            <Route path="api-logs" element={<ApiLogPage />} />
            <Route path="entities" element={<Entities />} />
            <Route path="queue" element={<Queue />} />
            <Route path="recommendations" element={<Recommendations />} />
            <Route path="settings" element={<Settings />} />
            <Route path="help" element={<Help />} />
          </Route>
        </Route>
        {/* Unknown URIs — redirect unauthenticated users to /library, else 404 */}
        <Route path="*" element={<UnknownRoute authState={authState} />} />
      </Routes>
    </BrowserRouter>
  )
}
