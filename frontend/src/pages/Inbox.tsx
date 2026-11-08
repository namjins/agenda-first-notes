import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useWs } from '../ws'
import type { FollowUp } from '../types'
import { ErrorBox, Section, copyText, useAsync } from '../components/ui'

const STATUSES = ['proposed', 'open', 'blocked', 'deferred', 'completed', 'unverified'] as const

export default function Inbox() {
  const { ws } = useWs()
  const items = useAsync(() => api.get<FollowUp[]>(api.w(ws, '/follow-ups')), [ws])
  const [filter, setFilter] = useState<string>('active')
  const [err, setErr] = useState<unknown>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<string | null>(null)
  const [desc, setDesc] = useState('')

  const list = useMemo(() => {
    const all = items.data ?? []
    if (filter === 'active') return all.filter((f) => ['proposed', 'open', 'blocked', 'unverified'].includes(f.status))
    if (filter === 'repeated') { const seen = new Map<string, number>(); all.forEach((f) => seen.set(f.description.toLowerCase(), (seen.get(f.description.toLowerCase()) ?? 0) + 1)); return all.filter((f) => (seen.get(f.description.toLowerCase()) ?? 0) > 1) }
    if (filter === 'all') return all
    return all.filter((f) => f.status === filter)
  }, [items.data, filter])

  const update = async (id: string, body: Record<string, unknown>) => { try { await api.patch(api.w(ws, `/follow-ups/${id}`), body); items.reload() } catch (e) { setErr(e) } }

  const prepareAgenda = async () => {
    const chosen = (items.data ?? []).filter((f) => picked.has(f.id))
    const text = chosen.map((f) => f.description + (f.owner ? ` (${f.owner})` : '')).join('\n')
    if (await copyText(text)) alert(`Copied ${chosen.length} item(s) as agenda lines.`)
    else prompt('Copy these agenda lines:', text)
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-3 flex items-center justify-between"><h1 className="text-xl font-semibold">Follow-Up Inbox</h1>
        <button className="btn btn-primary" disabled={picked.size === 0} onClick={prepareAgenda}>Prepare Next Agenda ({picked.size})</button></div>
      <ErrorBox error={err ?? items.error} />
      <div className="mb-3 flex flex-wrap gap-1">{['active', 'proposed', 'open', 'blocked', 'deferred', 'unverified', 'completed', 'repeated', 'all'].map((f) => <button key={f} className={`btn ${filter === f ? 'btn-primary' : ''}`} onClick={() => setFilter(f)}>{f}</button>)}</div>
      <Section title="Add a follow-up manually">
        <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (!desc.trim()) return; try { await api.post(api.w(ws, '/follow-ups'), { description: desc }); setDesc(''); items.reload() } catch (x) { setErr(x) } }}>
          <input className="input" placeholder="Description" value={desc} onChange={(e) => setDesc(e.target.value)} /><button className="btn">Add</button>
        </form>
      </Section>
      <ul className="space-y-2">
        {list.map((f) => (
          <li key={f.id} className="panel p-3 text-sm">
            <div className="flex items-start gap-3">
              <input type="checkbox" aria-label="Select for next agenda" className="mt-1" checked={picked.has(f.id)} onChange={(e) => { const s = new Set(picked); if (e.target.checked) s.add(f.id); else s.delete(f.id); setPicked(s) }} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`tag ${f.status === 'completed' ? 'tag-ok' : f.status === 'unverified' || f.status === 'proposed' ? 'tag-warn' : 'tag-muted'}`}>{f.status}</span>
                  <span className="muted text-xs">{f.origin === 'transcript' ? 'from transcript' : 'entered by user'}{f.agenda_topic ? ` · ${f.agenda_topic}` : ''}{f.meeting_id && <> · <Link className="underline" to={`/meetings/${f.meeting_id}`}>source meeting</Link></>}</span>
                </div>
                <input className="input mt-1" defaultValue={f.description} onBlur={(e) => e.target.value !== f.description && update(f.id, { description: e.target.value })} aria-label="Description" />
                <div className="mt-1 flex flex-wrap gap-2">
                  <input className="input w-40" placeholder="Owner (explicit only)" defaultValue={f.owner ?? ''} onBlur={(e) => (e.target.value || null) !== f.owner && update(f.id, { owner: e.target.value })} aria-label="Owner" />
                  <input className="input w-40" type="date" defaultValue={f.due_date ?? ''} onBlur={(e) => (e.target.value || null) !== f.due_date && update(f.id, { due_date: e.target.value })} aria-label="Due date" />
                  <select className="input w-36" value={f.status} onChange={(e) => { const note = e.target.value === 'completed' || e.target.value === 'open' ? prompt('Verification note (optional):') ?? '' : ''; update(f.id, { status: e.target.value, note }) }} aria-label="Status">{STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}</select>
                  <button className="btn" onClick={() => { const n = prompt('Verification note:'); if (n) update(f.id, { note: n }) }}>Add note</button>
                  <button className="btn" onClick={() => setExpanded(expanded === f.id ? null : f.id)}>{expanded === f.id ? 'Hide' : 'Evidence & history'}</button>
                </div>
                {expanded === f.id && (
                  <div className="mt-2 rounded-md p-2 text-xs" style={{ background: 'var(--bg)' }}>
                    <div className="mb-1 font-semibold">Evidence</div>
                    {f.evidence.length === 0 && <div className="muted">No transcript evidence (user-entered).</div>}
                    {f.evidence.map((e, i) => <blockquote key={i} className="muted border-l-2 pl-2" style={{ borderColor: 'var(--accent)' }}>“{e.quote}”</blockquote>)}
                    <div className="mb-1 mt-2 font-semibold">Status history</div>
                    {f.events.map((e) => <div key={e.id}><span className="muted">{new Date(e.created_at).toLocaleString()}</span> · {e.prev_status ?? '∅'} → {e.new_status} · {e.source}{e.note ? ` · ${e.note}` : ''}</div>)}
                  </div>
                )}
              </div>
            </div>
          </li>
        ))}
        {list.length === 0 && <p className="muted text-sm">Nothing here.</p>}
      </ul>
    </div>
  )
}
