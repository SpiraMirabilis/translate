import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Books from './pages/Books'
import ChapterEditor from './pages/ChapterEditor'
import Entities from './pages/Entities'
import Queue from './pages/Queue'
import Settings from './pages/Settings'
import Help from './pages/Help'
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

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const ws = new WebSocket(`ws://${location.host}/ws`)
    wsRef.current = ws

    ws.onopen    = () => { setConnected(true); clearTimeout(reconnectTimer.current) }
    ws.onclose   = () => {
      setConnected(false)
      reconnectTimer.current = setTimeout(connect, 2000)
    }
    ws.onerror   = () => ws.close()
    ws.onmessage = (e) => {
      try { setLastMessage(JSON.parse(e.data)) } catch { /* ignore */ }
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
    <WsContext.Provider value={{ lastMessage, connected }}>
      {children}
    </WsContext.Provider>
  )
}

// ------------------------------------------------------------------
// App
// ------------------------------------------------------------------
export default function App() {
  const [authChecked, setAuthChecked] = useState(false)
  const [needsLogin, setNeedsLogin] = useState(false)

  useEffect(() => {
    api.authStatus()
      .then(({ auth_required, authenticated }) => {
        setNeedsLogin(auth_required && !authenticated)
        setAuthChecked(true)
      })
      .catch(() => {
        // If auth check fails, assume no auth needed (e.g. backend down)
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
    <BrowserRouter>
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
    </BrowserRouter>
  )
}
