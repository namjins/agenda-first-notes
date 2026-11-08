import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useWs } from '../ws'
import type { AuditEvent, CallingEvent, Decision, Meeting, ScheduledEvent } from '../types'
import { ErrorBox, Section, StateTag, fmtDate, useAsync } from '../components/ui'

export default function History() {
  const { ws } = useWs()
  const [tab, setTab] = useState<'meetings' | 'decisions' | 'callings' | 'events' | 'audit'>('meetings')
  const meetings = useAsync(() => api.get<Meeting[]>(api.w(ws, '/meetings')), [ws])
  const decisions = useAsync(() => api.get<Decision[]>(api.w(ws, '/decisions')), [ws, tab])
  const callings = useAsync(() => api.get<{ types: string[]; events: CallingEvent[] }>(api.w(ws, '/calling-events')), [ws, tab])
  const events = useAsync(() => api.get<ScheduledEvent[]>(api.w(ws, '/events')), [ws, tab])
  const audit = useAsync(() => api.get<AuditEvent[]>(api.w(ws, '/audit')), [ws, tab])
  const [newEvent, setNewEvent] = useState({ person: '', calling: '', event_type: 'suggested', note: '' })
  const [err, setErr] = useState<unknown>(null)

  const tabs = ws === 'bishopric' ? ['meetings', 'decisions', 'callings', 'events', 'audit'] : ['meetings', 'decisions', 'events', 'audit']

  return (
    <div className="max-w-5xl">
      <h1 className="mb-3 text-xl font-semibold">Meeting History</h1>
      <div className="mb-4 flex gap-1">{tabs.map((t) => <button key={t} className={`btn ${tab === t ? 'btn-primary' : ''}`} onClick={() => setTab(t as typeof tab)}>{t[0].toUpperCase() + t.slice(1)}</button>)}</div>
      <ErrorBox error={err} />
      {tab === 'meetings' && (
        <Section title="Chronological meetings">
          <ErrorBox error={meetings.error} />
          <table className="w-full text-sm"><thead className="muted text-left text-xs"><tr><th className="py-1">Date</th><th>Title</th><th>State</th><th>Updated</th></tr></thead>
            <tbody>{(meetings.data ?? []).map((m) => <tr key={m.id} className="border-t" style={{ borderColor: 'var(--line)' }}><td className="py-1.5"><Link className="underline" to={`/meetings/${m.id}`}>{fmtDate(m.meeting_date)}</Link></td><td>{m.title}</td><td><StateTag state={m.state} /></td><td className="muted">{new Date(m.updated_at).toLocaleString()}</td></tr>)}</tbody></table>
          {meetings.data?.length === 0 && <p className="muted text-sm">No meetings yet.</p>}
        </Section>
      )}
      {tab === 'decisions' && (
        <Section title="Decisions (proposed vs confirmed)">
          <ul className="space-y-2 text-sm">{(decisions.data ?? []).map((d) => (
            <li key={d.id} className="flex items-start justify-between gap-3 border-t pt-2" style={{ borderColor: 'var(--line)' }}>
              <div><div>{d.text}</div><div className="muted text-xs">{d.agenda_topic ?? '—'} · <Link className="underline" to={`/meetings/${d.meeting_id}`}>meeting</Link> · origin: {d.origin}{d.evidence.length ? ` · ${d.evidence.length} evidence quote(s)` : ''}</div></div>
              <select className="input w-36" value={d.status} onChange={async (e) => { try { await api.patch(api.w(ws, `/decisions/${d.id}`), { status: e.target.value }); decisions.reload() } catch (x) { setErr(x) } }}>
                <option value="proposed">proposed</option><option value="confirmed">confirmed</option><option value="unverified">unverified</option>
              </select>
            </li>))}</ul>
          {decisions.data?.length === 0 && <p className="muted text-sm">No decisions recorded.</p>}
        </Section>
      )}
      {tab === 'callings' && (
        <>
          <Section title="Add an administrative calling event (explicit, audited)">
            <div className="grid gap-2 md:grid-cols-5">
              <input className="input" placeholder="Person (optional)" value={newEvent.person} onChange={(e) => setNewEvent({ ...newEvent, person: e.target.value })} />
              <input className="input" placeholder="Calling" value={newEvent.calling} onChange={(e) => setNewEvent({ ...newEvent, calling: e.target.value })} />
              <select className="input" value={newEvent.event_type} onChange={(e) => setNewEvent({ ...newEvent, event_type: e.target.value })}>{(callings.data?.types ?? []).map((t) => <option key={t} value={t}>{t.replaceAll('_', ' ')}</option>)}</select>
              <input className="input" placeholder="Note" value={newEvent.note} onChange={(e) => setNewEvent({ ...newEvent, note: e.target.value })} />
              <button className="btn btn-primary" onClick={async () => { try { await api.post(api.w(ws, '/calling-events'), newEvent); setNewEvent({ person: '', calling: '', event_type: 'suggested', note: '' }); callings.reload() } catch (x) { setErr(x) } }}>Add</button>
            </div>
            <p className="muted mt-2 text-xs">Each state is independent: suggested ≠ approved ≠ extended ≠ accepted ≠ sustained ≠ set apart. Nothing advances automatically. This tracker is not a substitute for official records.</p>
          </Section>
          <Section title="Calling events">
            <table className="w-full text-sm"><thead className="muted text-left text-xs"><tr><th>When</th><th>Person</th><th>Calling</th><th>Event</th><th>Note</th><th>Origin</th></tr></thead>
              <tbody>{(callings.data?.events ?? []).map((c) => <tr key={c.id} className="border-t" style={{ borderColor: 'var(--line)' }}><td className="py-1.5 muted">{new Date(c.created_at).toLocaleDateString()}</td><td>{c.person ?? <span className="muted">unclear</span>}</td><td>{c.calling ?? '—'}</td><td><span className="tag tag-muted">{c.event_type.replaceAll('_', ' ')}</span></td><td>{c.note}</td><td className="muted">{c.origin}{c.meeting_id && <> · <Link className="underline" to={`/meetings/${c.meeting_id}`}>meeting</Link></>}</td></tr>)}</tbody></table>
          </Section>
        </>
      )}
      {tab === 'events' && (
        <Section title="Scheduled events (dates are never overwritten; changes are recorded)">
          <ul className="space-y-2 text-sm">{(events.data ?? []).map((e) => (
            <li key={e.id} className="flex items-center justify-between gap-3 border-t pt-2" style={{ borderColor: 'var(--line)' }}>
              <div>{e.title} <span className="muted">· {e.event_date ?? 'date not stated'}</span>{e.supersedes_id && <span className="tag tag-warn ml-2">changed</span>}</div>
              <form className="flex gap-1" onSubmit={async (ev) => { ev.preventDefault(); const f = new FormData(ev.currentTarget); try { await api.post(api.w(ws, `/events/${e.id}/date`), { event_date: f.get('d'), note: f.get('n') }); events.reload() } catch (x) { setErr(x) } }}>
                <input name="d" className="input w-32" placeholder="New date" required /><input name="n" className="input w-40" placeholder="Reason" /><button className="btn">Record change</button>
              </form>
            </li>))}</ul>
          {events.data?.length === 0 && <p className="muted text-sm">No scheduled events recorded.</p>}
        </Section>
      )}
      {tab === 'audit' && (
        <Section title="Audit log (append-only)">
          <table className="w-full text-xs"><thead className="muted text-left"><tr><th>When</th><th>Entity</th><th>Action</th><th>Source</th><th>Change</th></tr></thead>
            <tbody>{(audit.data ?? []).map((a) => <tr key={a.id} className="border-t align-top" style={{ borderColor: 'var(--line)' }}><td className="py-1 muted">{new Date(a.created_at).toLocaleString()}</td><td>{a.entity_type}</td><td>{a.action}</td><td>{a.source}</td><td className="font-mono">{a.prev ? JSON.stringify(a.prev) + ' → ' : ''}{JSON.stringify(a.new)}</td></tr>)}</tbody></table>
        </Section>
      )}
    </div>
  )
}
