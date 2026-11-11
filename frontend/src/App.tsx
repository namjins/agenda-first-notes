import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api, hasToken } from './api'
import { WsProvider, useWs } from './ws'
import type { WorkspaceId } from './types'
import { isTypingTarget } from './components/ui'
import Dashboard from './pages/Dashboard'
import MeetingPage from './pages/MeetingPage'
import History from './pages/History'
import Inbox from './pages/Inbox'
import SearchPage from './pages/SearchPage'
import References from './pages/References'
import Members from './pages/Members'
import SettingsPage from './pages/SettingsPage'

const NAV = [
  ['/', 'Dashboard', '1'],
  ['/new', 'New Meeting', '2'],
  ['/history', 'Meeting History', '3'],
  ['/inbox', 'Follow-Up Inbox', '4'],
  ['/search', 'Search', '5'],
  ['/references', 'Reference Documents', '6'],
  ['/members', 'Members', '7'],
  ['/settings', 'Settings', '8'],
] as const

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark' | 'system'>(() => { try { return (localStorage.getItem('afn_theme') as 'light' | 'dark' | 'system') || 'system' } catch { return 'system' } })
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => document.documentElement.classList.toggle('dark', theme === 'dark' || (theme === 'system' && mq.matches))
    apply()
    mq.addEventListener('change', apply)
    try { localStorage.setItem('afn_theme', theme) } catch { /* ignore */ }
    return () => mq.removeEventListener('change', apply)
  }, [theme])
  return { theme, setTheme }
}

function Shell() {
  const { ws, setWs, wsName } = useWs()
  const { theme, setTheme } = useTheme()
  const nav = useNavigate()
  const [health, setHealth] = useState<'ok' | 'down' | 'unauth'>('ok')

  useEffect(() => {
    api.get('/api/workspaces').then(() => setHealth('ok')).catch((e) => setHealth(e.status === 401 ? 'unauth' : 'down'))
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return
      if (e.altKey || e.metaKey || e.ctrlKey) return
      const hit = NAV.find((n) => n[2] === e.key)
      if (hit) { e.preventDefault(); nav(hit[0]) }
      if (e.key === 'w') { e.preventDefault(); setWs(ws === 'ward_council' ? 'bishopric' : 'ward_council'); nav('/') }
      if (e.key === '?') { e.preventDefault(); alert('Shortcuts: 1-8 pages · w switch workspace · g generate · a approve · c copy Markdown · ? help') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nav, ws, setWs])

  const switchWs = (w: WorkspaceId) => { if (w !== ws) { setWs(w); nav('/') } } // workspace-specific UI state is dropped on switch

  return (
    <div className="flex h-full min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r p-3" style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}>
        <div className="mb-3 text-sm font-semibold tracking-tight">Agenda First Notes</div>
        <label className="muted mb-1 text-xs" htmlFor="ws-select">Workspace</label>
        <select id="ws-select" className="input mb-4 font-medium" value={ws} onChange={(e) => switchWs(e.target.value as WorkspaceId)} data-testid="workspace-select">
          <option value="ward_council">Ward Council</option>
          <option value="bishopric">Bishopric</option>
        </select>
        <nav className="flex flex-col gap-0.5" aria-label="Main">
          {NAV.map(([to, label, key]) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `flex items-center justify-between rounded-md px-2 py-1.5 text-sm ${isActive ? 'font-semibold' : 'muted hover:text-current'}`} style={({ isActive }) => (isActive ? { background: 'color-mix(in srgb, var(--accent) 12%, transparent)' } : {})}>
              <span>{label}</span><span className="kbd muted">{key}</span>
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-2 text-xs">
          {health === 'down' && <div className="tag tag-bad block">Backend unreachable</div>}
          {health === 'unauth' && <div className="tag tag-bad block">Missing access token — open the launch URL</div>}
          <label className="muted block" htmlFor="theme">Theme</label>
          <select id="theme" className="input" value={theme} onChange={(e) => setTheme(e.target.value as 'light' | 'dark' | 'system')}>
            <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
          </select>
          <div className="muted">Local only · no cloud · <span className="kbd">?</span> shortcuts</div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto p-6" key={ws}>
        <div className="muted mb-3 text-xs uppercase tracking-wide">{wsName}</div>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<MeetingPage />} />
          <Route path="/meetings/:id" element={<MeetingPage />} />
          <Route path="/history" element={<History />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/references" element={<References />} />
          <Route path="/members" element={<Members />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  if (!hasToken() && !import.meta.env.DEV) {
    return (
      <div className="mx-auto max-w-lg p-10">
        <h1 className="mb-2 text-lg font-semibold">Access token required</h1>
        <p className="muted text-sm">Open Agenda First Notes using the full URL printed in the terminal when you launched it (it ends with <code>#token=…</code>). This keeps other programs on this computer from reading your notes.</p>
      </div>
    )
  }
  return <WsProvider><Shell /></WsProvider>
}
