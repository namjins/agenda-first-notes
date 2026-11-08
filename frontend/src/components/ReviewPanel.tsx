import { useMemo, useState } from 'react'
import { api } from '../api'
import { flatten } from '../agendaTree'
import type { Finding, Note, ReviewView, WorkspaceId } from '../types'
import { ErrorBox } from './ui'

const STATUS_LABEL: Record<Note['status'], [string, string]> = {
  verified: ['Source verified', 'tag-ok'],
  uncertain: ['Uncertain', 'tag-warn'],
  user_edited: ['User corrected', 'tag-muted'],
  needs_review: ['Requires review', 'tag-bad'],
  unsupported: ['Unsupported — excluded', 'tag-bad'],
  manual_unverified: ['Manual, unverified', 'tag-warn'],
}

export default function ReviewPanel({ ws, view, transcript, onChange, readOnly }: { ws: WorkspaceId; view: ReviewView; transcript: string; onChange: () => void; readOnly: boolean }) {
  const mid = view.meeting.id
  const [issuesOnly, setIssuesOnly] = useState(false)
  const [selected, setSelected] = useState<Note | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [adding, setAdding] = useState<string | null>(null)
  const tree = view.meeting.agenda?.tree ?? []
  const rows = useMemo(() => flatten(tree), [tree])
  const findingsByNote = useMemo(() => {
    const m = new Map<string, Finding[]>()
    view.findings.forEach((f) => { if (f.note_id) m.set(f.note_id, [...(m.get(f.note_id) ?? []), f]) })
    return m
  }, [view.findings])
  const hasIssue = (n: Note) => n.status !== 'verified' && n.status !== 'user_edited' || n.stale || (findingsByNote.get(n.id) ?? []).some((f) => !f.resolved && f.severity !== 'info')
  const live = view.notes.filter((n) => !n.deleted)
  const notesFor = (nodeId: string | null) => live.filter((n) => n.agenda_node_id === nodeId && (!issuesOnly || hasIssue(n)))
  const generalFindings = view.findings.filter((f) => !f.note_id)
  const openHigh = view.findings.filter((f) => f.severity === 'high' && !f.resolved && (!f.note_id || live.some((n) => n.id === f.note_id)))

  const patch = async (n: Note, body: Record<string, unknown>) => { try { await api.patch(api.w(ws, `/meetings/${mid}/notes/${n.id}`), body); onChange() } catch (e) { setErr(e) } }
  const resolve = async (f: Finding, resolved: boolean) => { try { await api.post(api.w(ws, `/meetings/${mid}/findings/${f.id}/resolve`), { resolved }); onChange() } catch (e) { setErr(e) } }
  const addNote = async (nodeId: string | null, text: string) => { if (!text.trim()) return; try { await api.post(api.w(ws, `/meetings/${mid}/notes`), { text, agenda_node_id: nodeId }); setAdding(null); onChange() } catch (e) { setErr(e) } }
  const toggleCarry = async (id: string, on: boolean) => { const cur = new Set(view.meeting.carry_forward); if (on) cur.add(id); else cur.delete(id); try { await api.patch(api.w(ws, `/meetings/${mid}`), { carry_forward: [...cur] }); onChange() } catch (e) { setErr(e) } }

  const highlighted = useMemo(() => {
    if (!selected || selected.evidence.length === 0) return null
    const spans = [...selected.evidence].sort((a, b) => a.start - b.start)
    const parts: { text: string; mark: boolean }[] = []
    let pos = 0
    for (const s of spans) { if (s.start < pos) continue; parts.push({ text: transcript.slice(pos, s.start), mark: false }); parts.push({ text: transcript.slice(s.start, s.end), mark: true }); pos = s.end }
    parts.push({ text: transcript.slice(pos), mark: false })
    return parts
  }, [selected, transcript])

  const NoteRow = ({ n }: { n: Note }) => {
    const [label, cls] = STATUS_LABEL[n.status]
    const fs = (findingsByNote.get(n.id) ?? []).filter((f) => !f.resolved)
    const isSel = selected?.id === n.id
    return (
      <li className={`rounded-md border p-2 text-sm ${isSel ? 'ring-2' : ''}`} style={{ borderColor: 'var(--line)', ['--tw-ring-color' as string]: 'var(--accent)' }} data-testid="note">
        <div className="flex flex-wrap items-center gap-1.5">
          <button className={`tag ${cls}`} onClick={() => setSelected(isSel ? null : n)} title="Show evidence">{label}</button>
          {n.stale && <span className="tag tag-bad">Source changed — revalidate</span>}
          <span className="tag tag-muted">{n.category.replaceAll('_', ' ')}</span>
          {n.calling_event && <span className="tag tag-muted">calling: {n.calling_event.replaceAll('_', ' ')}</span>}
          <span className="muted ml-auto text-xs">{n.evidence.length} quote(s)</span>
        </div>
        {readOnly ? <p className="mt-1">{n.text}{n.follow_up && ' [Follow-up]'}{n.owner && ` [Owner: ${n.owner}]`}</p> : (
          <>
            <textarea className="input mt-1 resize-y" rows={2} defaultValue={n.text} aria-label="Note text" onBlur={(e) => e.target.value.trim() !== n.text && patch(n, { text: e.target.value })} />
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
              <label className="flex items-center gap-1"><input type="checkbox" checked={n.follow_up} onChange={(e) => patch(n, { follow_up: e.target.checked })} /> Follow-up</label>
              <input className="input w-44" placeholder="Owner (only if explicit)" defaultValue={n.owner ?? ''} onBlur={(e) => (e.target.value || null) !== n.owner && patch(n, { owner: e.target.value || null })} aria-label="Owner" />
              <select className="input w-44" value={n.agenda_node_id ?? ''} onChange={(e) => patch(n, { agenda_node_id: e.target.value || null })} aria-label="Agenda item"><option value="">(unmapped)</option>{rows.map((r) => <option key={r.node.id} value={r.node.id}>{'· '.repeat(r.depth)}{r.node.text}</option>)}</select>
              <button className="btn btn-danger" onClick={() => patch(n, { deleted: true })}>Remove</button>
            </div>
          </>
        )}
        {fs.map((f) => (
          <div key={f.id} className="mt-1 flex items-start gap-2 text-xs"><span className={`tag ${f.severity === 'high' ? 'tag-bad' : 'tag-warn'}`}>{f.category}</span><span className="flex-1">{f.message}</span>{!readOnly && <button className="underline" onClick={() => resolve(f, true)}>Dismiss</button>}</div>
        ))}
      </li>
    )
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1"><input type="checkbox" checked={issuesOnly} onChange={(e) => setIssuesOnly(e.target.checked)} /> Issues only</label>
          <span className="muted">{live.length} note(s) · {openHigh.length} unresolved high-severity finding(s)</span>
          {view.coverage && !view.coverage.complete && <span className="tag tag-bad">Transcript coverage incomplete — absence of discussion is not confirmed</span>}
        </div>
        <ErrorBox error={err} />
        <ul className="space-y-3">
          {rows.map(({ node, depth }) => {
            const ns = notesFor(node.id)
            if (issuesOnly && ns.length === 0) return null
            return (
              <li key={node.id} style={{ marginLeft: depth * 14 }}>
                <div className="flex items-center justify-between"><span className="font-medium">{node.text}</span>{!readOnly && node.kind === 'topic' && <button className="muted text-xs underline" onClick={() => setAdding(adding === node.id ? null : node.id)}>+ add note</button>}</div>
                {adding === node.id && <form className="mt-1 flex gap-1" onSubmit={(e) => { e.preventDefault(); addNote(node.id, new FormData(e.currentTarget).get('t') as string) }}><input name="t" className="input" placeholder="Manual note (will be marked unverified unless it matches the transcript)" autoFocus /><button className="btn">Add</button></form>}
                <ul className="mt-1 space-y-1">{ns.map((n) => <NoteRow key={n.id} n={n} />)}</ul>
                {ns.length === 0 && node.kind === 'topic' && !issuesOnly && <p className="muted text-xs">Not discussed.</p>}
              </li>
            )
          })}
          {notesFor(null).length > 0 && (
            <li><div className="font-medium">Unmapped discussion <span className="tag tag-warn">needs an agenda item or removal</span></div><ul className="mt-1 space-y-1">{notesFor(null).map((n) => <NoteRow key={n.id} n={n} />)}</ul></li>
          )}
        </ul>
        {view.undiscussed_follow_ups.length > 0 && (
          <div className="panel mt-4 p-3 text-sm">
            <div className="font-medium">On the agenda, not discussed, still open in history</div>
            <p className="muted text-xs">Status is unchanged. Tick an item to carry it forward into this meeting's follow-up list explicitly.</p>
            {view.undiscussed_follow_ups.map((u) => <label key={u.follow_up_id} className="mt-1 flex items-center gap-2"><input type="checkbox" disabled={readOnly} checked={u.selected} onChange={(e) => toggleCarry(u.follow_up_id, e.target.checked)} /><span>{u.agenda_text}</span><span className="tag tag-muted">{u.status}</span></label>)}
          </div>
        )}
      </div>
      <div className="space-y-4">
        <div className="panel p-3">
          <div className="mb-1 text-sm font-semibold">Review findings</div>
          {view.findings.length === 0 && <p className="muted text-sm">No findings recorded.</p>}
          <ul className="max-h-64 space-y-1 overflow-auto text-xs">
            {[...generalFindings, ...view.findings.filter((f) => f.note_id)].filter((f) => !issuesOnly || !f.resolved).map((f) => (
              <li key={f.id} className={`flex items-start gap-2 ${f.resolved ? 'opacity-50' : ''}`}><span className="muted w-8">r{f.round_no}</span><span className={`tag ${f.severity === 'high' ? 'tag-bad' : f.severity === 'low' ? 'tag-warn' : 'tag-muted'}`}>{f.category}</span><span className="flex-1">{f.message}{f.action && f.action !== 'none' ? ` (${f.action})` : ''}</span>{!readOnly && f.severity !== 'info' && <button className="underline" onClick={() => resolve(f, !f.resolved)}>{f.resolved ? 'Reopen' : 'Dismiss'}</button>}</li>
            ))}
          </ul>
        </div>
        <div className="panel p-3">
          <div className="mb-1 flex items-center justify-between text-sm font-semibold"><span>Transcript evidence</span>{selected && <button className="muted text-xs underline" onClick={() => setSelected(null)}>clear</button>}</div>
          {!selected && <p className="muted text-xs">Click a note's status tag to highlight its supporting passages.</p>}
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap text-xs leading-relaxed" data-testid="evidence">{highlighted ? highlighted.map((p, i) => p.mark ? <mark key={i} id={i === 1 ? 'first-mark' : undefined}>{p.text}</mark> : <span key={i}>{p.text}</span>) : transcript}</pre>
        </div>
      </div>
    </div>
  )
}
