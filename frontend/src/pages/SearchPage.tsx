import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useWs } from '../ws'
import type { SearchHit } from '../types'
import { ErrorBox } from '../components/ui'

export default function SearchPage() {
  const { ws, wsName } = useWs()
  const [q, setQ] = useState('')
  const [type, setType] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [err, setErr] = useState<unknown>(null)

  const run = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!q.trim()) return
    const p = new URLSearchParams({ q })
    if (type) p.set('entity_type', type)
    if (from) p.set('date_from', from)
    if (to) p.set('date_to', to)
    try { setHits(await api.get<SearchHit[]>(api.w(ws, `/search?${p}`))); setErr(null) } catch (x) { setErr(x) }
  }

  return (
    <div className="max-w-4xl">
      <h1 className="mb-1 text-xl font-semibold">Search</h1>
      <p className="muted mb-3 text-sm">Searches approved notes, decisions, follow-ups and calling events in {wsName} only.</p>
      <form onSubmit={run} className="mb-4 flex flex-wrap gap-2">
        <input className="input flex-1" placeholder="Keyword, topic, decision…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        <select className="input w-40" value={type} onChange={(e) => setType(e.target.value)}><option value="">All types</option><option value="meeting">Meeting notes</option><option value="decision">Decisions</option><option value="follow_up">Follow-ups</option><option value="calling_event">Calling events</option></select>
        <input className="input w-36" type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From" /><input className="input w-36" type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="To" />
        <button className="btn btn-primary">Search</button>
      </form>
      <ErrorBox error={err} />
      {hits && hits.length === 0 && <p className="muted text-sm">No matches.</p>}
      <ul className="space-y-2">{(hits ?? []).map((h, i) => (
        <li key={i} className="panel p-3 text-sm">
          <div className="flex items-center gap-2"><span className="tag tag-muted">{h.entity_type.replaceAll('_', ' ')}</span><span className="muted text-xs">{h.meeting_date || 'undated'} · {h.status}</span><Link className="ml-auto text-xs underline" to={`/meetings/${h.meeting_id}`}>Open meeting</Link></div>
          <div className="mt-1 font-medium">{h.title}</div>
          <div className="muted mt-0.5" dangerouslySetInnerHTML={{ __html: h.snippet.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!).replace(/\[(.+?)\]/g, '<mark>$1</mark>') }} />
        </li>))}</ul>
    </div>
  )
}
