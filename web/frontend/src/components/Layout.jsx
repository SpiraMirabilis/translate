import { Outlet, NavLink } from 'react-router-dom'
import { useWs } from '../App'
import {
  Languages, BookOpen, Database, ListChecks, Settings, HelpCircle, Wifi, WifiOff
} from 'lucide-react'

const nav = [
  { to: '/',         icon: Languages,   label: 'Translate' },
  { to: '/books',    icon: BookOpen,     label: 'Books'     },
  { to: '/entities', icon: Database,     label: 'Entities'  },
  { to: '/queue',    icon: ListChecks,   label: 'Queue'     },
  { to: '/settings', icon: Settings,     label: 'Settings'  },
  { to: '/help',     icon: HelpCircle,   label: 'Help'      },
]

export default function Layout() {
  const { connected } = useWs()

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-14 flex flex-col items-center py-4 gap-1 bg-slate-950 border-r border-slate-800 shrink-0">
        {/* Logo */}
        <div className="mb-4 text-indigo-400 font-bold font-mono text-lg select-none">T9</div>

        {nav.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            title={label}
            className={({ isActive }) =>
              `w-10 h-10 flex items-center justify-center rounded-lg transition-colors
               ${isActive
                 ? 'bg-indigo-600 text-white'
                 : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'}`
            }
          >
            <Icon size={18} />
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
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
