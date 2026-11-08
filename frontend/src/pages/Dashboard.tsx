import { Link } from 'react-router-dom'
import { api } from '../api'
import { useWs } from '../ws'
import type { FollowUp, Meeting, ModelsResponse } from '../types'
import { ErrorBox, Section, StateTag, fmtDate, useAsync } from '../components/ui'

export default function Dashboard() {
  const { ws, wsName } = useWs()
  const meetings = useAsync(() => api.get<Meeting[]>(api.w(ws, '/meetings?limit=8')), [ws])
  const inbox = useAsync(() => api.get<FollowUp[]>(api.w(ws, '/follow-ups')), [ws])
  const models = useAsync(() => api.get<ModelsResponse>('/api/models'), [])
  const unfinished = (meetings.data ?? []).filter((m) => m.state !== 'approved' && m.state !== 'archived')
  const open = (inbox.data ?? []).filter((f) => ['open', 'proposed', 'blocked', 'unverified'].includes(f.status))

  return (
    <div className="max-w-4xl">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">{wsName}</h1>
        <Link to="/new" className="btn btn-primary">New Meeting</Link>
      </div>
      <ErrorBox error={meetings.error} />
      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Resume unfinished meetings">
          {unfinished.length === 0 && <p className="muted text-sm">Nothing in progress.</p>}
          <ul className="space-y-1">{unfinished.map((m) => (
            <li key={m.id}><Link to={`/meetings/${m.id}`} className="flex items-center justify-between rounded px-1 py-1 text-sm hover:underline"><span>{m.title || fmtDate(m.meeting_date)}</span><StateTag state={m.state} /></Link></li>
          ))}</ul>
        </Section>
        <Section title="Local model" right={<Link to="/settings" className="text-xs underline">Settings</Link>}>
          {models.data ? (
            <div className="text-sm">
              <div>{models.data.connected ? <span className="tag tag-ok">Ollama connected</span> : <span className="tag tag-bad">Ollama not reachable</span>}</div>
              <div className="muted mt-1">{models.data.models.length} local model(s) installed</div>
              {models.data.warnings.map((w) => <div key={w} className="tag tag-warn mt-1 block">{w}</div>)}
            </div>
          ) : <ErrorBox error={models.error} />}
        </Section>
        <Section title="Open follow-ups" right={<Link to="/inbox" className="text-xs underline">Inbox</Link>}>
          {open.length === 0 && <p className="muted text-sm">No open follow-ups.</p>}
          <ul className="space-y-1 text-sm">{open.slice(0, 8).map((f) => <li key={f.id} className="flex justify-between gap-2"><span className="truncate">{f.description}</span><span className="tag tag-muted shrink-0">{f.status}</span></li>)}</ul>
        </Section>
        <Section title="Recent meetings" right={<Link to="/history" className="text-xs underline">All</Link>}>
          <ul className="space-y-1 text-sm">{(meetings.data ?? []).slice(0, 8).map((m) => <li key={m.id}><Link to={`/meetings/${m.id}`} className="flex justify-between hover:underline"><span>{fmtDate(m.meeting_date)}{m.title ? ` · ${m.title}` : ''}</span><StateTag state={m.state} /></Link></li>)}</ul>
        </Section>
      </div>
    </div>
  )
}
