import { useEffect, useState } from 'react'
import { api } from '../api'
import { flatten, updateText } from '../agendaTree'
import type { AgendaNameSuggestion, AgendaNode, NameSuggestions as Data, WorkspaceId } from '../types'
import { ErrorBox, useAsync } from './ui'

// Confirm-first: nothing is applied until the user confirms a name and regenerates the notes.
export default function NameSuggestions({ ws, meetingId, version, readOnly, onChange }: { ws: WorkspaceId; meetingId: string; version: number; readOnly: boolean; onChange?: () => void }) {
  const data = useAsync(() => api.get<Data>(api.w(ws, `/meetings/${meetingId}/name-suggestions`)), [ws, meetingId, version])
  const [err, setErr] = useState<unknown>(null)
  const d = data.data
  const done = () => { setErr(null); data.reload(); onChange?.() } // removing a confirmation re-checks the notes
  const answer = async (heard: string, member_id: string, status: 'confirmed' | 'rejected') => {
    try { await api.post(api.w(ws, `/meetings/${meetingId}/name-confirmations`), { heard, member_id, status }); done() } catch (e) { setErr(e) }
  }
  const undo = async (id: string) => { try { await api.del(api.w(ws, `/meetings/${meetingId}/name-confirmations/${id}`)); done() } catch (e) { setErr(e) } }
  if (!d?.answers) return <ErrorBox error={data.error} /> // an odd reply must never take the meeting page down
  return (
    <div className="space-y-3 text-sm">
      <p className="muted text-xs">Suggestions only, from the member list and the context around each mention. Confirm a name only if you know who was meant. Confirmed names are used when you regenerate the notes and are labelled “Name confirmed by you”, never “Source verified”.</p>
      <ErrorBox error={err} />
      {d.needs_regeneration && !readOnly && <div className="tag tag-warn block">Confirmed names changed since the notes were generated. Regenerate the notes to apply them.</div>}
      {d.answers.length > 0 && (
        <ul className="space-y-1">{d.answers.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center gap-2">
            <span className={`tag ${a.status === 'confirmed' ? 'tag-ok' : 'tag-muted'}`}>{a.status === 'confirmed' ? 'Confirmed by you' : 'Not this person'}</span>
            <span>“{a.heard}” → {a.name}</span>
            {!a.in_transcript && <span className="tag tag-bad">No longer in the transcript</span>}
            {!readOnly && <button className="muted text-xs underline" aria-label={`${a.status === 'confirmed' ? 'Remove' : 'Undo'} ${a.name} for “${a.heard}”`} onClick={() => undo(a.id)}>{a.status === 'confirmed' ? 'Remove' : 'Undo'}</button>}
          </li>))}</ul>
      )}
      {d.member_count === 0 ? <p className="muted text-sm">No member list loaded. Load one on the Members page to get name suggestions.</p>
        : d.suggestions.length === 0 && <p className="muted">No name suggestions for this transcript.</p>}
      {d.suggestions.map((s) => (
        <div key={s.heard} className="rounded-md border p-2" style={{ borderColor: 'var(--line)' }} data-testid="name-suggestion">
          <div>Heard: <span className="font-medium">“{s.heard}”</span>{s.count > 1 && <span className="muted"> · {s.count} mentions</span>}</div>
          {s.quotes.map((q, i) => <blockquote key={i} className="muted my-1 border-l-2 pl-2 text-xs" style={{ borderColor: 'var(--line)' }}>{q}</blockquote>)}
          {s.count > 1 && <div className="text-xs">A confirmation applies to all {s.count} mentions in this meeting.</div>}
          <div className="muted text-xs">{s.agenda_item ? <>Agenda item: {s.agenda_item}</> : 'Agenda item: unclear'}{s.orgs.length > 0 && <> · Organisations mentioned nearby: {s.orgs.join(', ')}</>}</div>
          <ul className="mt-1 space-y-1">{s.candidates.map((c) => (
            <li key={c.member_id} className="flex flex-wrap items-start gap-2">
              <div className="min-w-0 flex-1"><span className="font-medium">{c.preferred}</span>{c.name !== c.preferred && <span className="muted"> ({c.name})</span>}{c.callings.length > 0 && <span className="muted"> · {c.callings.join('; ')}</span>}<div className="muted text-xs">Why: {c.reason}</div></div>
              {!readOnly && <>
                <button className="btn" aria-label={`Confirm ${c.preferred} for “${s.heard}”`} onClick={() => answer(s.heard, c.member_id, 'confirmed')}>Confirm</button>
                <button className="btn" aria-label={`Not ${c.preferred} for “${s.heard}”`} onClick={() => answer(s.heard, c.member_id, 'rejected')}>Not this person</button>
              </>}
            </li>))}</ul>
        </div>
      ))}
    </div>
  )
}

// Draft agenda only. Suggest-only: "Fix spelling" edits the heading exactly as typing in the editor would (saved with
// the user's next save or approval); the app never changes a heading by itself.
export function AgendaNameCheck({ ws, meetingId, tree, onChange }: { ws: WorkspaceId; meetingId: string; tree: AgendaNode[]; onChange: (t: AgendaNode[]) => void }) {
  const key = JSON.stringify(tree)
  const [sent, setSent] = useState(key)
  useEffect(() => { const t = setTimeout(() => setSent(key), 400); return () => clearTimeout(t) }, [key]) // not on every keystroke
  const data = useAsync(() => api.post<AgendaNameSuggestion[]>(api.w(ws, `/meetings/${meetingId}/agenda/name-check`), { tree: JSON.parse(sent) }), [ws, meetingId, sent])
  const [dismissed, setDismissed] = useState<string[]>([])
  const nodes = new Map(flatten(tree).map((f) => [f.node.id, f.node]))
  // Python counts code points, JS UTF-16 units (an emoji before the name shifts it right): find the name itself
  const at = (s: AgendaNameSuggestion) => nodes.get(s.node_id)?.text.indexOf(s.written, s.start) ?? -1
  const shown = (Array.isArray(data.data) ? data.data : []).filter((s) => at(s) >= 0 && !dismissed.includes(`${s.node_id} ${s.written}`))
  if (!shown.length) return null // a failed check only means no suggestions: the agenda works without it
  return (
    <div className="mt-2 space-y-1 text-sm" data-testid="agenda-name-check">
      {shown.map((s) => s.candidates.map((c) => (
        <div key={s.node_id + c.member_id} className="flex flex-wrap items-center gap-2">
          <span>Agenda says “{s.written}” — member list has “{c.name}”{c.callings.length > 0 && ` (${c.callings.join('; ')})`}. Fix spelling?</span>
          <button className="btn" aria-label={`Change “${s.written}” to “${c.name}”`} onClick={() => { const t = nodes.get(s.node_id)!.text, i = at(s); onChange(updateText(tree, s.node_id, t.slice(0, i) + c.name + t.slice(i + s.written.length))) }}>Fix spelling</button>
          <button className="btn" aria-label={`Keep “${s.written}”`} onClick={() => setDismissed((d) => [...d, `${s.node_id} ${s.written}`])}>Dismiss</button>
        </div>)))}
    </div>
  )
}
