import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError, api, describeError } from '../api'
import { useWs } from '../ws'
import type { AgendaNode, Job, Meeting, ReviewView } from '../types'
import AgendaEditor from '../components/AgendaEditor'
import ReviewPanel from '../components/ReviewPanel'
import { ErrorBox, Section, StateTag, copyText, fmtDate, isTypingTarget, useAsync } from '../components/ui'

// Images are fetched with the token header and shown as blob URLs; the token never goes into a URL.
export function AuthImage({ src }: { src: string }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let obj: string | null = null
    let live = true
    fetch(src, { headers: { 'X-AFN-Token': sessionStorage.getItem('afn_token') ?? '' } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => { if (live) { obj = URL.createObjectURL(b); setUrl(obj) } }) // unmounted first: never create it
      .catch(() => { if (live) setUrl(null) })
    return () => { live = false; if (obj) URL.revokeObjectURL(obj) }
  }, [src])
  return url ? <img alt="Agenda screenshot" className="h-40 rounded border" style={{ borderColor: 'var(--line)' }} src={url} /> : null
}

const STEPS = ['Agenda', 'Transcript', 'Generate', 'Review', 'Approve', 'Copy / Export']

function stepIndex(m: Meeting): number {
  if (m.state === 'approved') return 5
  if (m.state === 'ready_for_approval') return 4
  if (m.state === 'review_required' || m.state === 'failed') return 3
  if (m.state === 'processing') return 2
  if (m.transcript && m.agenda_approved) return 2
  if (m.agenda_approved) return 1
  return 0
}

export default function MeetingPage() {
  const { id } = useParams()
  const { ws, wsName } = useWs()
  const nav = useNavigate()
  if (!id) return <NewMeeting ws={ws} wsName={wsName} onCreated={(m) => nav(`/meetings/${m.id}`)} />
  return <MeetingWorkflow key={`${ws}:${id}`} id={id} />
}

function NewMeeting({ ws, wsName, onCreated }: { ws: string; wsName: string; onCreated: (m: Meeting) => void }) {
  const [title, setTitle] = useState('')
  const [date, setDate] = useState('')
  const [err, setErr] = useState<unknown>(null)
  return (
    <div className="max-w-lg">
      <h1 className="mb-3 text-xl font-semibold">New {wsName} meeting</h1>
      <form className="panel space-y-3 p-4" onSubmit={async (e) => { e.preventDefault(); try { onCreated(await api.post<Meeting>(api.w(ws as 'ward_council', '/meetings'), { title, meeting_date: date || null })) } catch (x) { setErr(x) } }}>
        <label className="block text-sm"><span className="muted block text-xs">Title (optional)</span><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={`${wsName} meeting`} /></label>
        <label className="block text-sm"><span className="muted block text-xs">Meeting date — leave empty if not yet known; it is never guessed</span><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <ErrorBox error={err} />
        <button className="btn btn-primary">Create meeting</button>
      </form>
    </div>
  )
}

function MeetingWorkflow({ id }: { id: string }) {
  const { ws } = useWs()
  const nav = useNavigate()
  const meeting = useAsync(() => api.get<Meeting>(api.w(ws, `/meetings/${id}`)), [ws, id])
  const m = meeting.data
  const [err, setErr] = useState<unknown>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [review, setReview] = useState<ReviewView | null>(null)
  const [transcriptText, setTranscriptText] = useState<string>('')
  const [transcriptLoaded, setTranscriptLoaded] = useState(false)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle')
  const [copied, setCopied] = useState(false)
  const [agendaMode, setAgendaMode] = useState<'tree' | 'text' | 'image'>('tree')
  const [agendaText, setAgendaText] = useState('')
  const [agendaTree, setAgendaTree] = useState<AgendaNode[] | null>(null)
  const [imageInfo, setImageInfo] = useState<string>('')
  const saveTimer = useRef<number | null>(null)
  const pendingTranscript = useRef<string | null>(null)

  const refreshReview = useCallback(async () => { try { setReview(await api.get<ReviewView>(api.w(ws, `/meetings/${id}/review`))) } catch (e) { setErr(e) } }, [ws, id])
  const reloadAll = useCallback(async () => { meeting.reload(); await refreshReview() }, [meeting, refreshReview])

  // Load transcript text once; load review when notes may exist.
  useEffect(() => {
    if (!m) return
    if (m.transcript && !transcriptLoaded) api.get<{ text: string }>(api.w(ws, `/meetings/${id}/transcript`)).then((t) => { setTranscriptText(t.text); setTranscriptLoaded(true) }).catch(setErr)
    if (!m.transcript) setTranscriptLoaded(true)
    if (['review_required', 'ready_for_approval', 'approved', 'failed', 'transcript_added'].includes(m.state)) refreshReview()
    if (m.agenda && agendaTree === null) { setAgendaTree(m.agenda.tree); setAgendaText(m.agenda.text) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m?.id, m?.state, m?.transcript?.version_no, m?.agenda?.id])

  // Resume a running job after reload.
  useEffect(() => { api.get<Job[]>(api.w(ws, `/meetings/${id}/jobs`)).then((js) => { const active = js.find((j) => j.status === 'queued' || j.status === 'running'); if (active) setJob(active) }).catch(() => {}) }, [ws, id])

  // Poll the job.
  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return
    const t = window.setTimeout(async () => { try { const j = await api.get<Job>(api.w(ws, `/jobs/${job.id}`)); setJob(j); if (j.status !== 'queued' && j.status !== 'running') { await reloadAll() } } catch (e) { setErr(e) } }, 1000)
    return () => window.clearTimeout(t)
  }, [job, ws, reloadAll])

  // Clipboard screenshot paste (Cmd/Ctrl+V) anywhere on the page while in agenda step.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const items = Array.from(e.clipboardData?.items ?? []).filter((i) => i.type.startsWith('image/'))
      if (items.length === 0 || !m || m.agenda_approved) return
      e.preventDefault()
      items.forEach((i) => { const f = i.getAsFile(); if (f) uploadImage(f) })
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m?.id, m?.agenda_approved])

  const saveAgenda = async (tree: AgendaNode[] | null, text: string | null, source: string): Promise<boolean> => {
    try { const r = await api.put<Meeting>(api.w(ws, `/meetings/${id}/agenda`), tree ? { tree, source } : { text, source }); meeting.setData(r); setAgendaTree(r.agenda!.tree); setAgendaText(r.agenda!.text); setErr(null); return true } catch (e) { setErr(e); return false }
  }
  // Never approve when the edited tree was not saved: that would approve an older revision than the one on screen.
  const approveAgenda = async () => { if (agendaTree && !(await saveAgenda(agendaTree, null, m?.agenda ? 'edit' : 'manual'))) return; try { meeting.setData(await api.post<Meeting>(api.w(ws, `/meetings/${id}/agenda/approve`))); setErr(null) } catch (e) { setErr(e) } }
  const uploadImage = async (f: File) => {
    setImageInfo('Extracting text from image…')
    try {
      const r = await api.upload<{ proposed_text: string | null; backend: string | null; error: string | null }>(api.w(ws, `/meetings/${id}/agenda/images`), f)
      if (r.proposed_text) { const merged = (agendaText ? agendaText + '\n' : '') + r.proposed_text; setAgendaText(merged); setAgendaMode('text'); setImageInfo(`Text extracted with ${r.backend}. Check it, then “Use this text”.`) }
      else setImageInfo(r.error ?? 'No text extracted; enter the agenda manually.')
      meeting.reload()
    } catch (e) { setImageInfo(describeError(e)) }
  }
  const persistTranscript = async (text: string): Promise<boolean> => {
    try {
      const needsReason = !!m && ['review_required', 'ready_for_approval', 'failed'].includes(m.state)
      const reason = needsReason ? 'Transcript corrected after notes were generated' : undefined
      meeting.setData(await api.put<Meeting>(api.w(ws, `/meetings/${id}/transcript`), { text, reason })); setSaveState('saved'); setErr(null)
      return true
    } catch (e) {
      setSaveState('failed')
      if (text.trim()) setErr(new Error(`Transcript not saved: ${describeError(e)}`))
      return !text.trim() // an emptied box is not a save the user is waiting on
    }
  }
  const cancelTranscriptSave = () => { if (saveTimer.current) window.clearTimeout(saveTimer.current); saveTimer.current = null; pendingTranscript.current = null }
  // Run a pending debounced save now, so processing never starts on an older version than the one on screen.
  const flushTranscript = async (): Promise<boolean> => { const text = pendingTranscript.current; cancelTranscriptSave(); return text === null || persistTranscript(text) }
  const saveTranscript = (text: string) => {
    setTranscriptText(text)
    setSaveState('saving')
    cancelTranscriptSave()
    pendingTranscript.current = text
    saveTimer.current = window.setTimeout(() => { void flushTranscript() }, 1200)
  }
  const generate = async () => { try { setErr(null); if (!(await flushTranscript())) return; setJob(await api.post<Job>(api.w(ws, `/meetings/${id}/process`))) } catch (e) { setErr(e) } }
  const cancel = async () => { if (job) try { setJob(await api.post<Job>(api.w(ws, `/jobs/${job.id}/cancel`))) } catch (e) { setErr(e) } }
  const approve = async () => { try { await api.post(api.w(ws, `/meetings/${id}/approve`)); await reloadAll(); setErr(null) } catch (e) { setErr(e) } }
  const reopen = async () => { try { await api.post(api.w(ws, `/meetings/${id}/reopen`)); await reloadAll() } catch (e) { setErr(e) } }
  const copyMd = async () => { if (!review) return; const ok = await copyText(review.markdown); setCopied(ok); if (!ok) prompt('Copy:', review.markdown); setTimeout(() => setCopied(false), 1500) }
  const download = async (fmt: 'md' | 'txt') => {
    const res = await fetch(api.w(ws, `/meetings/${id}/export?fmt=${fmt}`), { headers: { 'X-AFN-Token': sessionStorage.getItem('afn_token') ?? '' } })
    if (!res.ok) { const d = await res.json().catch(() => null); setErr(new ApiError(res.status, d?.detail ?? res.statusText)); return } // never save an error body as notes
    const blob = await res.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = res.headers.get('content-disposition')?.match(/filename="(.+)"/)?.[1] ?? `notes.${fmt}`; a.click(); URL.revokeObjectURL(a.href)
  }
  const remove = async () => { if (!confirm('Delete this meeting and all its artifacts? This cannot be undone.')) return; try { await api.del(api.w(ws, `/meetings/${id}`)); nav('/') } catch (e) { setErr(e) } }
  const patchMeeting = async (body: Record<string, unknown>) => { try { meeting.setData(await api.patch<Meeting>(api.w(ws, `/meetings/${id}`), body)) } catch (e) { setErr(e) } }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'g' && canGenerate) generate()
      if (e.key === 'a' && m && (m.state === 'ready_for_approval' || m.state === 'review_required')) approve()
      if (e.key === 'c' && review) copyMd()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!m) return <ErrorBox error={meeting.error} />
  const busy = !!job && (job.status === 'queued' || job.status === 'running')
  const canGenerate = !!m.transcript && m.agenda_approved && !busy && m.state !== 'approved'
  const approved = m.state === 'approved'
  const step = stepIndex(m)

  return (
    <div className="max-w-6xl">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <input className="input w-72 text-lg font-semibold" defaultValue={m.title} placeholder="Meeting title" aria-label="Title" onBlur={(e) => e.target.value !== m.title && patchMeeting({ title: e.target.value })} disabled={approved} />
        <label className="flex items-center gap-2 text-sm"><span className="muted text-xs">Meeting date</span>
          <input className="input w-40" type="date" value={m.meeting_date ?? ''} aria-label="Meeting date" onChange={(e) => patchMeeting({ meeting_date: e.target.value || null })} disabled={approved} />
          {!m.meeting_date && <span className="tag tag-warn">not confirmed</span>}</label>
        <StateTag state={m.state} />
        <div className="ml-auto flex gap-2"><button className="btn btn-danger" onClick={remove}>Delete meeting</button></div>
      </div>
      <ol className="mb-4 flex flex-wrap gap-1 text-xs" aria-label="Workflow">{STEPS.map((s, i) => <li key={s} className={`tag ${i < step ? 'tag-ok' : i === step ? 'tag-warn' : 'tag-muted'}`}>{i + 1}. {s}</li>)}</ol>
      <ErrorBox error={err} />
      {m.last_error && m.state === 'failed' && <div className="tag tag-bad mb-3 block p-2">Last processing error: {m.last_error}</div>}

      <Section title={`1. Agenda ${m.agenda_approved ? '(approved — revision ' + m.agenda!.revision_no + ')' : m.agenda ? '(revision ' + m.agenda.revision_no + ', not yet approved)' : ''}`}
        right={!approved && (m.agenda_approved
          ? <button className="btn" onClick={() => saveAgenda(agendaTree, null, 'edit')} disabled={busy}>Edit agenda (new revision)</button>
          : <div className="flex gap-1">{(['tree', 'text', 'image'] as const).map((k) => <button key={k} className={`btn ${agendaMode === k ? 'btn-primary' : ''}`} onClick={() => setAgendaMode(k)}>{k === 'tree' ? 'Tree' : k === 'text' ? 'Text' : 'Screenshot'}</button>)}</div>)}>
        {m.agenda_approved || approved ? (
          <pre className="whitespace-pre-wrap text-sm">{m.agenda?.text}</pre>
        ) : (
          <>
            {agendaMode === 'image' && (
              <div>
                <div className="muted rounded-md border border-dashed p-6 text-center text-sm" style={{ borderColor: 'var(--line)' }} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); Array.from(e.dataTransfer.files).forEach(uploadImage) }}>
                  Drop PNG / JPEG / WebP screenshots here, paste with <span className="kbd">⌘V</span> / <span className="kbd">Ctrl+V</span>, or
                  <label className="btn ml-2 cursor-pointer">choose files<input type="file" accept="image/png,image/jpeg,image/webp" multiple className="hidden" onChange={(e) => Array.from(e.target.files ?? []).forEach(uploadImage)} /></label>
                </div>
                {imageInfo && <p className="muted mt-2 text-xs">{imageInfo}</p>}
                {m.images.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{m.images.map((img) => <AuthImage key={img.id} src={api.w(ws, `/meetings/${id}/agenda/images/${img.id}`)} />)}</div>}
              </div>
            )}
            {agendaMode === 'text' && (
              <div>
                <textarea className="input min-h-48 font-mono" value={agendaText} onChange={(e) => setAgendaText(e.target.value)} placeholder={'One agenda item per line; indent with spaces for sub-items.\nOrganizations\n  Relief Society\n  Elders Quorum'} aria-label="Agenda text" />
                <div className="mt-2 flex gap-2"><button className="btn" onClick={async () => { await saveAgenda(null, agendaText, 'text'); setAgendaMode('tree') }}>Use this text</button></div>
              </div>
            )}
            {agendaMode === 'tree' && (
              <div>
                <AgendaEditor tree={agendaTree ?? []} onChange={setAgendaTree} />
                {!m.agenda && m.transcript && <button className="btn mt-2" onClick={async () => { try { setErr(null); const r = await api.post<{ tree: AgendaNode[]; meeting: Meeting }>(api.w(ws, `/meetings/${id}/agenda/propose`)); meeting.setData(r.meeting); setAgendaTree(r.tree); if (r.tree.length === 0) setErr(new Error('No substantive topics identified in the transcript; add agenda items manually.')) } catch (e) { setErr(e) } }}>Propose agenda from transcript (no agenda supplied)</button>}
              </div>
            )}
            <div className="mt-3 flex items-center gap-2">
              <button className="btn btn-primary" onClick={approveAgenda} disabled={!(agendaTree && agendaTree.length)} data-testid="approve-agenda">Approve agenda</button>
              <span className="muted text-xs">Headings are rendered exactly as approved; notes can only be placed under them.</span>
            </div>
          </>
        )}
      </Section>

      <Section title={`2. Transcript ${m.transcript ? `(version ${m.transcript.version_no}, ${m.transcript.length.toLocaleString()} chars)` : ''}`}
        right={<span className="muted text-xs">{saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : saveState === 'failed' ? 'Not saved' : 'Autosaves'}</span>}>
        {approved ? <details><summary className="muted cursor-pointer text-sm">Show transcript</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-xs">{transcriptText}</pre></details> : (
          <>
            <textarea className="input min-h-64 font-mono text-xs" value={transcriptText} onChange={(e) => saveTranscript(e.target.value)} placeholder="Paste the transcript here (plain text). The original is preserved; later edits create a new version." aria-label="Transcript" disabled={busy} data-testid="transcript" />
            <div className="mt-2 flex items-center gap-2 text-xs"><label className="btn cursor-pointer">Upload .txt / .md<input type="file" accept=".txt,.md,.markdown" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) { cancelTranscriptSave(); try { meeting.setData(await api.upload<Meeting>(api.w(ws, `/meetings/${id}/transcript/upload`), f)); setTranscriptText(await f.text()) } catch (x) { setErr(x) } } }} /></label>
              {m.transcript && m.transcript.version_no > 1 && <span className="muted">Corrections are versioned; notes whose evidence no longer matches are flagged for revalidation.</span>}</div>
          </>
        )}
      </Section>

      {!approved && (
        <Section title="3. Generate notes" right={<span className="muted text-xs">Runs locally · <span className="kbd">g</span></span>}>
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn btn-primary" onClick={generate} disabled={!canGenerate} data-testid="generate">{review && review.notes.length > 0 ? 'Regenerate (keeps your edits)' : 'Generate notes'}</button>
            {busy && <button className="btn" onClick={cancel}>Cancel</button>}
            {job?.status === 'failed' && <button className="btn" onClick={generate}>Retry</button>}
            {!m.agenda_approved && <span className="muted text-xs">Approve the agenda first.</span>}
            {!m.transcript && <span className="muted text-xs">Add a transcript first.</span>}
          </div>
          {job && (
            <div className="mt-3">
              <div className="h-2 w-full overflow-hidden rounded" style={{ background: 'var(--line)' }}><div className="h-2 transition-all" style={{ width: `${Math.round(job.progress * 100)}%`, background: job.status === 'failed' ? 'var(--bad)' : 'var(--accent)' }} /></div>
              <div className="muted mt-1 text-xs">{job.status}: {job.message}</div>
            </div>
          )}
        </Section>
      )}

      {review && (review.notes.length > 0 || review.findings.length > 0 || approved) && (
        <Section title={approved ? 'Approved notes' : '4. Review evidence and resolve issues'} right={m.coverage && <span className="muted text-xs">{m.coverage.segments.length} segment(s) · {m.coverage.complete ? 'fully processed' : 'INCOMPLETE'}</span>}>
          <ReviewPanel ws={ws} view={review} transcript={transcriptText} onChange={refreshReview} readOnly={approved || m.state === 'processing'} />
        </Section>
      )}

      {review && m.coverage && (
        <Section title={approved ? '6. Copy / Export' : '5. Approve'} right={<span className="muted text-xs"><span className="kbd">a</span> approve · <span className="kbd">c</span> copy</span>}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {!approved && <button className="btn btn-primary" onClick={approve} disabled={!(m.state === 'ready_for_approval' || m.state === 'review_required')} data-testid="approve-notes">Approve final notes</button>}
            {approved && <button className="btn" onClick={reopen}>Reopen for changes</button>}
            <button className="btn" onClick={copyMd} data-testid="copy-md">{copied ? 'Copied!' : 'Copy Markdown'}</button>
            <button className="btn" onClick={() => download('md')}>Export .md</button>
            <button className="btn" onClick={() => download('txt')}>Export .txt</button>
            {!m.meeting_date && <span className="tag tag-warn">Confirm the meeting date before approving</span>}
            {m.state === 'review_required' && <span className="tag tag-bad">Review required: resolve or dismiss high-severity findings first</span>}
          </div>
          <pre className="panel overflow-auto whitespace-pre-wrap p-3 text-sm" data-testid="markdown" style={{ background: 'var(--bg)' }}>{review.markdown}</pre>
          {!approved && <p className="muted mt-2 text-xs">Preview of {fmtDate(m.meeting_date)} notes. Only approved notes and the follow-up section are exported; review findings stay in the audit.</p>}
        </Section>
      )}
    </div>
  )
}
