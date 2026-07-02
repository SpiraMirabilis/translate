import {
  createBrowserRouter, createRoutesFromElements, RouterProvider,
  Route, Navigate, Outlet,
} from 'react-router-dom'
import {
  useState, useEffect, useRef, useCallback, createContext, useContext,
  lazy, Suspense,
} from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { queryClient } from './lib/queryClient'
import Layout from './components/Layout'
import WsQueryBridge from './components/WsQueryBridge'
// Public first-paint paths stay eager — readers should never wait on a
// second round trip for the shell they landed on.
import Reader from './pages/Reader'
import Library from './pages/Library'
import BookDetail from './pages/BookDetail'
import Login from './pages/Login'
import NotFound from './pages/NotFound'
import { api } from './services/api'

// Admin pages are route-split: they (and their heavy deps) load on demand,
// keeping the entry chunk lean for public readers.
const Dashboard       = lazy(() => import('./pages/Dashboard'))
const Books           = lazy(() => import('./pages/Books'))
const ChapterEditor   = lazy(() => import('./pages/ChapterEditor'))
const WriteEditor     = lazy(() => import('./pages/WriteEditor'))
const Entities        = lazy(() => import('./pages/Entities'))
const Queue           = lazy(() => import('./pages/Queue'))
const Settings        = lazy(() => import('./pages/Settings'))
const Help            = lazy(() => import('./pages/Help'))
const Recommendations = lazy(() => import('./pages/Recommendations'))
const CommentsAdmin   = lazy(() => import('./pages/CommentsAdmin'))
const ApiCalls        = lazy(() => import('./pages/ApiCalls'))
const ApiLogPage      = lazy(() => import('./pages/ApiLogPage'))
const ReaderStats     = lazy(() => import('./pages/ReaderStats'))

function PageSpinner() {
  return (
    <div className="flex justify-center items-center py-24">
      <Loader2 size={28} className="animate-spin text-indigo-400" />
    </div>
  )
}

// Wrap a lazy route element in the shared Suspense fallback.
const lazyEl = (el) => <Suspense fallback={<PageSpinner />}>{el}</Suspense>

// ------------------------------------------------------------------
// Site context — branding strings (site_name, public_site_name) shared app-wide
// ------------------------------------------------------------------
const SiteContext = createContext({ site_name: 'T9', public_site_name: 'Boonnovels' })
export const useSite = () => useContext(SiteContext)

function SiteProvider({ children }) {
  const [info, setInfo] = useState({ site_name: 'T9', public_site_name: 'Boonnovels' })
  useEffect(() => {
    api.getSiteInfo().then(setInfo).catch(e => console.warn('Failed to load site info:', e))
  }, [])
  return <SiteContext.Provider value={info}>{children}</SiteContext.Provider>
}

// ------------------------------------------------------------------
// Auth context — lets route elements (created once at module scope for the
// data router) read live auth state without the router being rebuilt.
// ------------------------------------------------------------------
const AuthContext = createContext({ authState: null, onLoginSuccess: () => {} })

// ------------------------------------------------------------------
// WebSocket context — single connection, all pages share it
// ------------------------------------------------------------------
const WsContext = createContext(null)
export const useWs = () => useContext(WsContext)

function WsProvider({ children }) {
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)
  const listenersRef = useRef(new Set())
  const everConnectedRef = useRef(false)

  const subscribe = useCallback((fn) => {
    listenersRef.current.add(fn)
    return () => listenersRef.current.delete(fn)
  }, [])

  // Deliver a message to every listener. Each call is isolated so one bad
  // listener can't prevent the rest from receiving the message.
  const deliver = useCallback((msg) => {
    listenersRef.current.forEach(fn => {
      try { fn(msg) } catch { /* ignore listener errors */ }
    })
  }, [])

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/ws`)
    wsRef.current = ws

    ws.onopen    = () => {
      setConnected(true)
      clearTimeout(reconnectTimer.current)
      // Synthetic event on RE-connect (not the first open) so consumers can
      // run one-shot catch-up (refetch status/lists missed while offline).
      if (everConnectedRef.current) deliver({ type: 'ws_reconnected' })
      everConnectedRef.current = true
    }
    ws.onclose   = () => {
      setConnected(false)
      reconnectTimer.current = setTimeout(connect, 2000)
    }
    ws.onerror   = () => ws.close()
    ws.onmessage = (e) => {
      let msg
      try {
        msg = JSON.parse(e.data)
      } catch { return /* ignore malformed frames */ }
      deliver(msg)
    }
  }, [deliver])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return (
    <WsContext.Provider value={{ connected, subscribe }}>
      {children}
    </WsContext.Provider>
  )
}

// ------------------------------------------------------------------
// Auth gate — pathless layout route that guards all admin routes
// ------------------------------------------------------------------
function AdminGate() {
  const { authState, onLoginSuccess } = useContext(AuthContext)
  const needsLogin = authState.auth_required && !authState.authenticated
  if (needsLogin) {
    return <Login onSuccess={onLoginSuccess} />
  }
  return (
    <WsProvider>
      <WsQueryBridge />
      <Outlet />
    </WsProvider>
  )
}

// Catch-all for unknown top-level URIs: send unauthenticated users to the
// public library (when enabled) instead of exposing the admin shell; show a
// 404 otherwise.
function UnknownRoute() {
  const { authState } = useContext(AuthContext)
  const needsLogin = authState.auth_required && !authState.authenticated
  if (needsLogin && authState.public_library) {
    return <Navigate to="/library" replace />
  }
  return <NotFound publicLibrary={authState.public_library} />
}

// ------------------------------------------------------------------
// Route tree — public routes are outside the auth gate (when enabled).
// Data router (createBrowserRouter) so ChapterEditor can use useBlocker for
// its unsaved-changes navigation guard. Built once at module scope; route
// elements read live auth state via AuthContext (context flows through
// RouterProvider), so the router never needs rebuilding.
// ------------------------------------------------------------------
const router = createBrowserRouter(createRoutesFromElements(
  <>
    {/* Public routes — gated server-side via auth middleware when public library is off */}
    <Route path="/library" element={<Library />} />
    <Route path="/library/book/:bookId" element={<BookDetail />} />
    <Route path="/library/read/:bookId/:chapterNum" element={<Reader isPublic />} />
    <Route path="/library/read/:bookId" element={<BookDetail />} />
    <Route path="/read/:bookId/:chapterNum" element={<Reader isPublic />} />
    <Route path="/read/:bookId" element={<Reader isPublic />} />
    {/* Admin routes — auth gated. Only specific paths are listed, so
        unknown URIs fall through to the catch-all below rather than
        resolving to the Dashboard. */}
    <Route element={<AdminGate />}>
      <Route path="/" element={<Layout />}>
        <Route index element={lazyEl(<Dashboard />)} />
        <Route path="books" element={lazyEl(<Books />)} />
        <Route path="books/:bookId" element={lazyEl(<Books />)} />
        <Route path="books/:bookId/chapters/:chapterNum/edit" element={lazyEl(<ChapterEditor />)} />
        <Route path="books/:bookId/chapters/:chapterNum/write" element={lazyEl(<WriteEditor />)} />
        <Route path="books/:bookId/api-calls" element={lazyEl(<ApiCalls />)} />
        <Route path="api-logs" element={lazyEl(<ApiLogPage />)} />
        <Route path="reader-stats" element={lazyEl(<ReaderStats />)} />
        <Route path="entities" element={lazyEl(<Entities />)} />
        <Route path="queue" element={lazyEl(<Queue />)} />
        <Route path="recommendations" element={lazyEl(<Recommendations />)} />
        <Route path="comments" element={lazyEl(<CommentsAdmin />)} />
        <Route path="settings" element={lazyEl(<Settings />)} />
        <Route path="help" element={lazyEl(<Help />)} />
      </Route>
    </Route>
    {/* Unknown URIs — redirect unauthenticated users to /library, else 404 */}
    <Route path="*" element={<UnknownRoute />} />
  </>
))

export default function App() {
  const [authState, setAuthState] = useState(null)

  useEffect(() => {
    api.authStatus()
      .then(setAuthState)
      .catch(() => setAuthState({ auth_required: false, authenticated: true, public_library: true }))
  }, [])

  // Session expiry: any authenticated API call that gets a 401 dispatches
  // this event (see services/api.js). Flip to unauthenticated so the Login
  // screen renders in place — the SPA is never unloaded, so ChapterEditor's
  // localStorage draft survives for restore after re-login.
  useEffect(() => {
    const onUnauthorized = () => {
      setAuthState(prev => (prev ? { ...prev, auth_required: true, authenticated: false } : prev))
    }
    window.addEventListener('api:unauthorized', onUnauthorized)
    return () => window.removeEventListener('api:unauthorized', onUnauthorized)
  }, [])

  if (!authState) {
    return <div className="min-h-screen bg-slate-900" />
  }

  const handleLoginSuccess = () => setAuthState({ ...authState, authenticated: true })

  return (
    <QueryClientProvider client={queryClient}>
      <SiteProvider>
        <AuthContext.Provider value={{ authState, onLoginSuccess: handleLoginSuccess }}>
          <RouterProvider router={router} />
        </AuthContext.Provider>
      </SiteProvider>
    </QueryClientProvider>
  )
}
