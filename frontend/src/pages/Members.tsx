import { useState } from 'react'
import { api } from '../api'
import type { Member } from '../types'
import { ErrorBox, Section, useAsync } from '../components/ui'

export default function Members() {
  const list = useAsync(() => api.get<{ count: number; members: Member[] }>('/api/members'), [])
  const [err, setErr] = useState<unknown>(null)
  const [warning, setWarning] = useState('')

  const upload = async (f: File | undefined) => {
    if (!f) return
    try { setWarning((await api.upload<{ warning?: string }>('/api/members', f)).warning ?? ''); setErr(null) } catch (e) { setErr(e) }
    list.reload()
  }
  const purge = async () => {
    if (!confirm('Delete the member list from this computer? It is the one list used by both Ward Council and Bishopric. Your “Not this person” answers in both are deleted with it. Names you already confirmed for meetings stay with those meetings.')) return
    try { setWarning((await api.del<{ warning?: string }>('/api/members')).warning ?? ''); setErr(null) } catch (e) { setErr(e) }
    list.reload()
  }

  return (
    <div className="max-w-4xl">
      <h1 className="mb-1 text-xl font-semibold">Members</h1>
      <p className="muted mb-3 text-sm">One member list, used by both Ward Council and Bishopric. It stays on this computer, in a local database of its own, and is used only by this app and the local model to suggest who a garbled name in a transcript may be. You confirm every suggestion yourself. The list is never sent online, exported, searchable, or treated as evidence of callings or of what was said.</p>
      <ErrorBox error={err ?? list.error} />
      {warning && <p className="tag tag-warn mb-3 block" role="status">{warning}</p>}
      <Section title="Load member list (CSV)">
        <label className="btn btn-primary cursor-pointer">Choose CSV<input type="file" accept=".csv,text/csv" className="hidden" aria-label="Member list CSV" onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = '' }} /></label>
        <p className="muted mt-2 text-xs">Columns: Preferred Name / Full Name / Spouse Name / Callings, with each sustained date in brackets. Loading a list replaces the previous one.</p>
      </Section>
      <Section title={`${list.data?.count ?? 0} member(s)`} right={!!list.data?.count && <button className="btn btn-danger" onClick={purge}>Delete member list</button>}>
        {list.data?.count === 0 ? <p className="muted text-sm">No member list loaded.</p> : (
          <table className="w-full text-sm"><thead className="muted text-left text-xs"><tr><th>Full name</th><th>Preferred</th><th>Spouse</th><th>Callings</th></tr></thead>
            <tbody>{(list.data?.members ?? []).map((m, i) => <tr key={i} className="border-t align-top" style={{ borderColor: 'var(--line)' }}>
              <td className="py-1.5">{m.full_name}</td><td>{m.preferred_name}</td><td>{m.spouse_name}</td><td>{m.callings.map((c) => c.calling).join('; ')}</td></tr>)}</tbody></table>
        )}
      </Section>
    </div>
  )
}
