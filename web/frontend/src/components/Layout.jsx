import { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import { useWs } from '../App'
import { api } from '../services/api'
import {
  Languages, BookOpen, Database, ListChecks, Settings, HelpCircle, Wifi, WifiOff, Menu, X, ScrollText, MessageSquarePlus
} from 'lucide-react'

const nav = [
  { to: '/',                icon: Languages,        label: 'Translate'       },
  { to: '/books',           icon: BookOpen,          label: 'Books'           },
  { to: '/entities',        icon: Database,          label: 'Entities'        },
  { to: '/queue',           icon: ListChecks,        label: 'Queue'           },
  { to: '/recommendations', icon: MessageSquarePlus, label: 'Recommendations', badgeKey: 'recs' },
  { to: '/api-logs',        icon: ScrollText,        label: 'API Logs'        },
  { to: '/settings',        icon: Settings,          label: 'Settings'        },
  { to: '/help',            icon: HelpCircle,        label: 'Help'            },
]

export default function Layout() {
  const { connected } = useWs()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [newRecsCount, setNewRecsCount] = useState(0)

  useEffect(() => {
    api.countRecommendations('new')
      .then(data => setNewRecsCount(data.count || 0))
      .catch(() => {})
    // Refresh every 5 minutes
    const interval = setInterval(() => {
      api.countRecommendations('new')
        .then(data => setNewRecsCount(data.count || 0))
        .catch(() => {})
    }, 5 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  const badges = { recs: newRecsCount }

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between px-3 py-2 bg-slate-950 border-b border-slate-800">
        <div className="text-indigo-400 font-bold font-mono text-lg select-none">T9</div>
        <div className="flex items-center gap-3">
          <div title={connected ? 'Connected' : 'Reconnecting…'}>
            {connected
              ? <Wifi size={14} className="text-emerald-400" />
              : <WifiOff size={14} className="text-rose-400 animate-pulse" />}
          </div>
          <button className="text-slate-300 p-1" onClick={() => setMobileOpen(v => !v)}>
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {/* Mobile nav overlay */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-30 bg-black/60" onClick={() => setMobileOpen(false)}>
          <div className="absolute top-12 left-0 right-0 bg-slate-950 border-b border-slate-800 py-2" onClick={e => e.stopPropagation()}>
            {nav.map(({ to, icon: Icon, label, badgeKey }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-5 py-3 text-sm transition-colors
                   ${isActive
                     ? 'bg-indigo-600/20 text-indigo-300'
                     : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'}`
                }
              >
                <Icon size={16} />
                {label}
                {badgeKey && badges[badgeKey] > 0 && (
                  <span className="ml-auto min-w-[20px] h-[20px] flex items-center justify-center rounded-full bg-rose-500 text-white text-[10px] font-bold px-1.5">
                    {badges[badgeKey] > 99 ? '99+' : badges[badgeKey]}
                  </span>
                )}
              </NavLink>
            ))}
          </div>
        </div>
      )}

      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-14 flex-col items-center py-4 gap-1 bg-slate-950 border-r border-slate-800 shrink-0">
        {/* Logo */}
        <div className="mb-4 text-indigo-400 font-bold font-mono text-lg select-none">T9</div>

        {nav.map(({ to, icon: Icon, label, badgeKey }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            title={label}
            className={({ isActive }) =>
              `relative w-10 h-10 flex items-center justify-center rounded-lg transition-colors
               ${isActive
                 ? 'bg-indigo-600 text-white'
                 : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'}`
            }
          >
            <Icon size={18} />
            {badgeKey && badges[badgeKey] > 0 && (
              <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-rose-500 text-white text-[10px] font-bold px-1">
                {badges[badgeKey] > 99 ? '99+' : badges[badgeKey]}
              </span>
            )}
          </NavLink>
        ))}

        {/* Connection indicator */}
        <div className="mt-auto" title={connected ? 'Connected' : 'Reconnecting…'}>
          {connected
            ? <Wifi size={14} className="text-emerald-400" />
            : <WifiOff size={14} className="text-rose-400 animate-pulse" />}
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto pt-12 md:pt-0">
        <Outlet />
      </main>
    </div>
  )
}
