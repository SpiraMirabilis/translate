import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react'
import { useAutoProcess } from './hooks/useAutoProcess'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Books from './pages/Books'
import ChapterEditor from './pages/ChapterEditor'
import Entities from './pages/Entities'
import Queue from './pages/Queue'
import Settings from './pages/Settings'
import Help from './pages/Help'

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
function AutoProcessListener() {
  useAutoProcess()
  return null
}

export default function App() {
  return (
    <BrowserRouter>
      <WsProvider>
        <AutoProcessListener />
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
