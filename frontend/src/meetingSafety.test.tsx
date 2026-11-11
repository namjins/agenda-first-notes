// Regression tests for review findings (SYNTHETIC data only).
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import ReviewPanel from './components/ReviewPanel'
import MeetingPage, { AuthImage } from './pages/MeetingPage'
import { WsProvider } from './ws'
import type { Meeting, Note, ReviewView } from './types'

const tree = [{ id: 'n1', text: 'Activity days', kind: 'topic' as const, children: [] }, { id: 'n2', text: 'Budget', kind: 'topic' as const, children: [] }]
const baseMeeting: Meeting = {
  id: 'm1', title: 'SYNTHETIC', meeting_date: '2026-03-01', state: 'ready_for_approval', created_at: '', updated_at: '',
  coverage: { segments: [], complete: true }, carry_forward: [], last_error: null,
  agenda: { id: 'a1', revision_no: 1, tree, source: 'text', approved_at: 'x', text: 'Activity days\nBudget' },
  agenda_approved: true, transcript: { id: 't1', version_no: 1, length: 9, source: 'paste' }, images: [],
}
const note = (id: string, text: string): Note => ({ id, meeting_id: 'm1', agenda_node_id: 'n2', position: 0, text, category: 'discussion', uncertainty: 'low', follow_up: false, owner: null, calling_event: null, status: 'verified', origin: 'model', evidence: [], stale: false, deleted: false })
const view = (meeting: Meeting = baseMeeting): ReviewView => ({ meeting, notes: [note('A', 'Note A'), note('B', 'Note B')], findings: [], coverage: meeting.coverage, markdown: '# SYNTHETIC', undiscussed_follow_ups: [] })

type Reply = { status?: number; body: unknown }
function mockApi(meeting: Meeting, override: (method: string, url: string) => Reply | undefined = () => undefined) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push(`${method} ${url}`)
    const r = override(method, url) ?? { body: url.endsWith('/review') ? view(meeting) : url.endsWith('/name-suggestions') ? { member_count: 0, suggestions: [], answers: [] } : url.endsWith('/jobs') ? [] : method === 'GET' && url.endsWith('/transcript') ? { text: 'SYNTHETIC' } : url.endsWith('/process') ? { id: 'j1', status: 'done', progress: 1, message: '' } : meeting }
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'content-type': 'application/json' } })
  }))
  render(<WsProvider><MemoryRouter initialEntries={['/meetings/m1']}><Routes><Route path="/meetings/:id" element={<MeetingPage />} /></Routes></MemoryRouter></WsProvider>)
  return calls
}

// jsdom has no blob URLs; give spyOn something to wrap.
URL.createObjectURL ??= () => ''
URL.revokeObjectURL ??= () => {}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

test('single-key shortcuts ignore a focused <select> (type-ahead "a" must not approve)', async () => {
  const calls = mockApi(baseMeeting)
  const sel = (await screen.findAllByLabelText('Agenda item'))[0]
  sel.focus()
  await act(async () => { fireEvent.keyDown(sel, { key: 'a' }) })
  expect(calls.some((c) => c.endsWith('/meetings/m1/approve'))).toBe(false)
  await act(async () => { fireEvent.keyDown(document.body, { key: 'a' }) })
  await waitFor(() => expect(calls).toContain('POST /api/w/ward_council/meetings/m1/approve'))
})

test('ReviewPanel keeps an in-progress edit when the review refreshes', () => {
  const props = { ws: 'ward_council' as const, transcript: '', onChange: () => {}, readOnly: false }
  const { rerender } = render(<ReviewPanel {...props} view={view()} />)
  const ta = screen.getAllByLabelText('Note text')[1] as HTMLTextAreaElement
  fireEvent.change(ta, { target: { value: 'Note B edited' } })
  rerender(<ReviewPanel {...props} view={view()} />)
  const after = screen.getAllByLabelText('Note text')[1] as HTMLTextAreaElement
  expect(after).toBe(ta)
  expect(after.value).toBe('Note B edited')
})

test('agenda approval stops when saving the edited tree fails, and the error stays visible', async () => {
  const unapproved: Meeting = { ...baseMeeting, state: 'agenda_imported', agenda_approved: false, transcript: null, coverage: null, agenda: { ...baseMeeting.agenda!, approved_at: null } }
  const calls = mockApi(unapproved, (method, url) => (method === 'PUT' && url.endsWith('/agenda') ? { status: 400, body: { detail: 'agenda item text cannot be empty' } } : undefined))
  const approveBtn = await screen.findByTestId('approve-agenda')
  await waitFor(() => expect(approveBtn).toBeEnabled()) // the tree loads after the button renders
  fireEvent.click(approveBtn)
  expect(await screen.findByText('agenda item text cannot be empty')).toBeInTheDocument()
  expect(calls.some((c) => c.endsWith('/agenda/approve'))).toBe(false)
})

test('Generate saves a pending transcript edit before processing starts', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'transcript_added', coverage: null }
  const calls = mockApi(ready)
  fireEvent.change(await screen.findByTestId('transcript'), { target: { value: 'SYNTHETIC corrected' } })
  fireEvent.click(screen.getByTestId('generate'))
  await waitFor(() => expect(calls.some((c) => c.endsWith('/process'))).toBe(true))
  const put = calls.findIndex((c) => c.startsWith('PUT') && c.endsWith('/transcript'))
  expect(put).toBeGreaterThan(-1)
  expect(put).toBeLessThan(calls.findIndex((c) => c.endsWith('/process')))
})

test('Generate does not start when the pending transcript save is refused', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'transcript_added', coverage: null }
  const calls = mockApi(ready, (method, url) => (method === 'PUT' && url.endsWith('/transcript') ? { status: 400, body: { detail: 'Meeting is processing' } } : undefined))
  fireEvent.change(await screen.findByTestId('transcript'), { target: { value: 'SYNTHETIC corrected' } })
  fireEvent.click(screen.getByTestId('generate'))
  expect(await screen.findByText('Transcript not saved: Meeting is processing')).toBeInTheDocument()
  expect(calls.some((c) => c.endsWith('/process'))).toBe(false)
})

test('export does not save an error response as a notes file', async () => {
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x')
  mockApi(baseMeeting, (_m, url) => (url.includes('/export') ? { status: 404, body: { detail: 'meeting not found' } } : undefined))
  fireEvent.click(await screen.findByText('Export .md'))
  expect(await screen.findByText('meeting not found')).toBeInTheDocument()
  expect(create).not.toHaveBeenCalled()
})

test('AuthImage creates no blob URL when it unmounts before the fetch resolves', async () => {
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x')
  let resolve: (r: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => { resolve = r })))
  const { unmount } = render(<AuthImage src="/api/w/ward_council/meetings/m1/agenda/images/i1" />)
  unmount()
  await act(async () => { resolve(new Response('x', { status: 200 })) })
  expect(create).not.toHaveBeenCalled()
})

test('Approve in Text mode saves the on-screen text, not the older tree', async () => {
  const unapproved: Meeting = { ...baseMeeting, state: 'agenda_imported', agenda_approved: false, transcript: null, coverage: null, agenda: { ...baseMeeting.agenda!, approved_at: null } }
  const calls = mockApi(unapproved)
  const bodies: string[] = []
  const f = globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }
  fireEvent.click(await screen.findByText('Text'))
  fireEvent.change(screen.getByLabelText('Agenda text'), { target: { value: 'Activity days\nSYNTHETIC new item' } })
  fireEvent.click(screen.getByTestId('approve-agenda'))
  await waitFor(() => expect(calls).toContain('POST /api/w/ward_council/meetings/m1/agenda/approve'))
  f.mock.calls.forEach(([, init]) => { if (init?.method === 'PUT') bodies.push(String(init.body)) })
  expect(bodies).toHaveLength(1)
  expect(JSON.parse(bodies[0])).toEqual({ text: 'Activity days\nSYNTHETIC new item', source: 'text' })
  expect(calls.findIndex((c) => c.startsWith('PUT'))).toBeLessThan(calls.findIndex((c) => c.endsWith('/agenda/approve')))
})

test('review notes are read-only from the moment Generate starts until the job finishes', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'review_required' }
  mockApi(ready, (_method, url) => (url.endsWith('/process') || url.includes('/jobs/j1') ? { body: { id: 'j1', status: 'running', progress: 0.1, message: 'SYNTHETIC' } } : undefined))
  expect((await screen.findAllByLabelText('Note text')).length).toBe(2)
  fireEvent.click(screen.getByTestId('generate'))
  await screen.findByText('running: SYNTHETIC')
  expect(screen.queryAllByLabelText('Note text')).toHaveLength(0)
  expect(screen.queryByText('Remove')).toBeNull()
})

test('Generate waits for an autosave already in flight and starts only if it succeeded', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'transcript_added', coverage: null }
  let release: (ok: boolean) => void = () => {}
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push(`${method} ${url}`)
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (method === 'PUT') return new Promise<Response>((r) => { release = (ok) => r(ok ? json(ready) : json({ detail: 'SYNTHETIC refused' }, 400)) })
    return json(url.endsWith('/jobs') ? [] : url.endsWith('/transcript') ? { text: 'SYNTHETIC' } : url.endsWith('/review') ? view(ready) : url.endsWith('/process') ? { id: 'j1', status: 'done', progress: 1, message: '' } : ready)
  }))
  render(<WsProvider><MemoryRouter initialEntries={['/meetings/m1']}><Routes><Route path="/meetings/:id" element={<MeetingPage />} /></Routes></MemoryRouter></WsProvider>)
  fireEvent.change(await screen.findByTestId('transcript'), { target: { value: 'SYNTHETIC corrected' } })
  await waitFor(() => expect(calls.some((c) => c.startsWith('PUT'))).toBe(true), { timeout: 3000 }) // debounce fired
  fireEvent.click(screen.getByTestId('generate'))
  await new Promise((r) => setTimeout(r, 50))
  expect(calls.some((c) => c.endsWith('/process'))).toBe(false)
  await act(async () => { release(false) })
  expect(await screen.findByText('Transcript not saved: SYNTHETIC refused')).toBeInTheDocument()
  expect(calls.some((c) => c.endsWith('/process'))).toBe(false)
  // The failed text stays pending: the next Generate retries the save and starts only once it succeeds.
  fireEvent.click(screen.getByTestId('generate'))
  await waitFor(() => expect(calls.filter((c) => c.startsWith('PUT'))).toHaveLength(2))
  await act(async () => { release(true) })
  await waitFor(() => expect(calls.some((c) => c.endsWith('/process'))).toBe(true))
})

const unapprovedNumbered: Meeting = { ...baseMeeting, state: 'agenda_imported', agenda_approved: false, transcript: null, coverage: null, agenda: { id: 'a1', revision_no: 1, tree: [{ id: 'n1', text: '1. Opening', kind: 'topic', children: [] }, { id: 'n2', text: 'Budget', kind: 'topic', children: [] }], source: 'text', approved_at: null, text: '1. Opening\nBudget' } }
const putBodies = () => (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls.filter(([, i]) => i?.method === 'PUT').map(([, i]) => JSON.parse(String(i!.body)))

test('Approve in Text mode without text edits saves the tree, keeping node ids and numbering', async () => {
  const calls = mockApi(unapprovedNumbered)
  await waitFor(() => expect(screen.getByTestId('approve-agenda')).toBeEnabled())
  fireEvent.click(screen.getByText('Text'))
  expect(screen.getByLabelText('Agenda text')).toHaveValue('1. Opening\nBudget')
  fireEvent.click(screen.getByTestId('approve-agenda'))
  await waitFor(() => expect(calls).toContain('POST /api/w/ward_council/meetings/m1/agenda/approve'))
  expect(putBodies()).toEqual([{ tree: unapprovedNumbered.agenda!.tree, source: 'edit' }])
})

test('unsaved tree edits survive switching to Text and approving there', async () => {
  const calls = mockApi(unapprovedNumbered)
  const inputs = await screen.findAllByLabelText('Agenda item at depth 0')
  await waitFor(() => expect(inputs[1]).toHaveValue('Budget'))
  fireEvent.change(inputs[1], { target: { value: 'Budget SYNTHETIC renamed' } })
  fireEvent.click(screen.getByText('Text'))
  expect(screen.getByLabelText('Agenda text')).toHaveValue('1. Opening\nBudget SYNTHETIC renamed')
  fireEvent.click(screen.getByTestId('approve-agenda'))
  await waitFor(() => expect(calls).toContain('POST /api/w/ward_council/meetings/m1/agenda/approve'))
  expect(putBodies()[0].tree[1]).toMatchObject({ id: 'n2', text: 'Budget SYNTHETIC renamed' })
})

test('unsaved agenda text is never dropped by switching to Tree without confirmation', async () => {
  mockApi(unapprovedNumbered)
  await waitFor(() => expect(screen.getByTestId('approve-agenda')).toBeEnabled())
  fireEvent.click(screen.getByText('Text'))
  fireEvent.change(screen.getByLabelText('Agenda text'), { target: { value: 'SYNTHETIC typed agenda' } })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  fireEvent.click(screen.getByText('Tree'))
  expect(confirm).toHaveBeenCalled()
  expect(screen.getByLabelText('Agenda text')).toHaveValue('SYNTHETIC typed agenda')
  confirm.mockReturnValue(true)
  fireEvent.click(screen.getByText('Tree'))
  expect(screen.queryByLabelText('Agenda text')).toBeNull()
  fireEvent.click(screen.getByText('Text'))
  expect(screen.getByLabelText('Agenda text')).toHaveValue('1. Opening\nBudget') // regenerated from the tree
})

test('everything is read-only while Generate flushes the transcript, before the job exists', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'review_required' }
  let release: () => void = () => {}
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push(`${method} ${url}`)
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    if (method === 'PUT') return new Promise<Response>((r) => { release = () => r(json(ready)) })
    return json(url.endsWith('/jobs') ? [] : url.endsWith('/transcript') ? { text: 'SYNTHETIC' } : url.endsWith('/review') ? view(ready) : url.endsWith('/process') || url.includes('/jobs/') ? { id: 'j1', status: 'queued', progress: 0, message: 'q' } : ready)
  }))
  render(<WsProvider><MemoryRouter initialEntries={['/meetings/m1']}><Routes><Route path="/meetings/:id" element={<MeetingPage />} /></Routes></MemoryRouter></WsProvider>)
  expect((await screen.findAllByLabelText('Note text')).length).toBe(2)
  fireEvent.change(screen.getByTestId('transcript'), { target: { value: 'SYNTHETIC v2' } })
  await waitFor(() => expect(screen.getByTestId('generate')).toBeEnabled())
  fireEvent.click(screen.getByTestId('generate'))
  await waitFor(() => expect(calls.some((c) => c.startsWith('PUT'))).toBe(true))
  expect(screen.getByTestId('transcript')).toBeDisabled()
  expect(screen.getByTestId('generate')).toBeDisabled()
  expect(screen.queryAllByLabelText('Note text')).toHaveLength(0)
  expect(calls.some((c) => c.endsWith('/process'))).toBe(false)
  await act(async () => { release() })
  await waitFor(() => expect(calls.some((c) => c.endsWith('/process'))).toBe(true))
  expect(screen.getByTestId('transcript')).toBeDisabled() // the queued job keeps it read-only
})

// OCR replies arrive in reverse order (later files are faster) unless uploads run one at a time.
function mountOcr(meeting: Meeting, ocr: (n: number) => string) {
  let n = 0
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push(`${method} ${url}`)
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
    if (method === 'POST' && url.endsWith('/agenda/images')) { const k = ++n; await new Promise((r) => setTimeout(r, 40 - 10 * k)); return json({ proposed_text: ocr(k), backend: 'fake', error: null }) }
    return json(url.endsWith('/jobs') ? [] : meeting)
  }))
  render(<WsProvider><MemoryRouter initialEntries={['/meetings/m1']}><Routes><Route path="/meetings/:id" element={<MeetingPage />} /></Routes></MemoryRouter></WsProvider>)
  return calls
}
const png = (name: string) => new File(['SYNTHETIC'], name, { type: 'image/png' })
const imageInput = () => document.querySelector('input[type=file][accept^="image"]') as HTMLInputElement
const agendaTextValue = () => (screen.getByLabelText('Agenda text') as HTMLTextAreaElement).value

test('screenshot OCR appends to unsaved tree edits instead of an older text', async () => {
  mountOcr(unapprovedNumbered, () => 'SYNTHETIC OCR')
  const inputs = await screen.findAllByLabelText('Agenda item at depth 0')
  await waitFor(() => expect(inputs[1]).toHaveValue('Budget'))
  fireEvent.change(inputs[1], { target: { value: 'Budget SYNTHETIC renamed' } })
  fireEvent.click(screen.getByText('Screenshot'))
  fireEvent.change(imageInput(), { target: { files: [png('a.png')] } })
  await waitFor(() => expect(agendaTextValue()).toBe('1. Opening\nBudget SYNTHETIC renamed\nSYNTHETIC OCR'))
})

test('several screenshots at once keep every page, in order', async () => {
  mountOcr(unapprovedNumbered, (k) => `SYNTHETIC page ${k}`)
  await waitFor(() => expect(screen.getByTestId('approve-agenda')).toBeEnabled())
  fireEvent.click(screen.getByText('Screenshot'))
  fireEvent.change(imageInput(), { target: { files: [png('a.png'), png('b.png'), png('c.png')] } })
  await waitFor(() => expect(agendaTextValue()).toBe('1. Opening\nBudget\nSYNTHETIC page 1\nSYNTHETIC page 2\nSYNTHETIC page 3'))
})

test('pasted screenshots append to typed agenda text, paste after paste', async () => {
  mountOcr(unapprovedNumbered, (k) => `SYNTHETIC pasted ${k}`)
  await waitFor(() => expect(screen.getByTestId('approve-agenda')).toBeEnabled())
  fireEvent.click(screen.getByText('Text'))
  fireEvent.change(screen.getByLabelText('Agenda text'), { target: { value: 'SYNTHETIC typed by hand' } })
  const paste = (f: File) => { const ev = new Event('paste', { cancelable: true }) as unknown as ClipboardEvent; Object.defineProperty(ev, 'clipboardData', { value: { items: [{ type: 'image/png', getAsFile: () => f }] } }); window.dispatchEvent(ev) }
  await act(async () => { paste(png('p1.png')) })
  await waitFor(() => expect(agendaTextValue()).toBe('SYNTHETIC typed by hand\nSYNTHETIC pasted 1'))
  await act(async () => { paste(png('p2.png')) })
  await waitFor(() => expect(agendaTextValue()).toBe('SYNTHETIC typed by hand\nSYNTHETIC pasted 1\nSYNTHETIC pasted 2'))
})

test('after a failed autosave, uploading a transcript file lets Generate start', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'transcript_added', coverage: null }
  const calls = mockApi(ready, (method, url) => (method === 'PUT' && url.endsWith('/transcript') ? { status: 400, body: { detail: 'SYNTHETIC refused' } } : undefined))
  fireEvent.change(await screen.findByTestId('transcript'), { target: { value: 'SYNTHETIC typed' } })
  await waitFor(() => expect(screen.getByText('Transcript not saved: SYNTHETIC refused')).toBeInTheDocument(), { timeout: 3000 })
  const file = new File(['SYNTHETIC uploaded transcript'], 't.txt', { type: 'text/plain' })
  fireEvent.change(document.querySelector('input[type=file][accept^=".txt"]') as HTMLInputElement, { target: { files: [file] } })
  await waitFor(() => expect(calls.some((c) => c.endsWith('/transcript/upload'))).toBe(true))
  await waitFor(() => expect(screen.getByTestId('transcript')).toHaveValue('SYNTHETIC uploaded transcript'))
  await waitFor(() => expect(screen.getByTestId('generate')).toBeEnabled())
  fireEvent.click(screen.getByTestId('generate'))
  await waitFor(() => expect(calls.some((c) => c.endsWith('/process'))).toBe(true))
})

test('closing the tab warns while agenda edits are unsaved', async () => {
  mockApi(unapprovedNumbered)
  const inputs = await screen.findAllByLabelText('Agenda item at depth 0')
  await waitFor(() => expect(inputs[1]).toHaveValue('Budget'))
  const unload = () => { const ev = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(ev); return ev.defaultPrevented }
  expect(unload()).toBe(false)
  fireEvent.change(inputs[1], { target: { value: 'Budget SYNTHETIC renamed' } })
  expect(unload()).toBe(true)
  fireEvent.change(inputs[1], { target: { value: 'Budget' } })
  expect(unload()).toBe(false)
  fireEvent.click(screen.getByText('Text'))
  fireEvent.change(screen.getByLabelText('Agenda text'), { target: { value: 'SYNTHETIC typed' } })
  expect(unload()).toBe(true)
})

test('a failed earlier autosave is not re-sent over a later successful one', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'transcript_added', coverage: null }
  const releases: ((ok: boolean) => void)[] = []
  const puts: string[] = []
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push(`${method} ${url}`)
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'content-type': 'application/json' } })
    if (method === 'PUT') { puts.push(JSON.parse(String(init!.body)).text); return new Promise<Response>((r) => { releases.push((ok) => r(ok ? json(ready) : json({ detail: 'SYNTHETIC busy' }, 503))) }) }
    return json(url.endsWith('/jobs') ? [] : url.endsWith('/transcript') ? { text: 'SYNTHETIC' } : url.endsWith('/review') ? view(ready) : url.endsWith('/process') ? { id: 'j1', status: 'done', progress: 1, message: '' } : ready)
  }))
  render(<WsProvider><MemoryRouter initialEntries={['/meetings/m1']}><Routes><Route path="/meetings/:id" element={<MeetingPage />} /></Routes></MemoryRouter></WsProvider>)
  const ta = await screen.findByTestId('transcript')
  fireEvent.change(ta, { target: { value: 'SYNTHETIC A' } })
  await waitFor(() => expect(puts).toEqual(['SYNTHETIC A']), { timeout: 3000 })
  fireEvent.change(ta, { target: { value: 'SYNTHETIC B' } })
  await act(async () => { await new Promise((r) => setTimeout(r, 1400)) }) // B's debounce fires and queues behind A
  await act(async () => { releases[0](false) })
  await waitFor(() => expect(puts).toEqual(['SYNTHETIC A', 'SYNTHETIC B']))
  await act(async () => { releases[1](true) })
  await waitFor(() => expect(screen.getByTestId('generate')).toBeEnabled())
  fireEvent.click(screen.getByTestId('generate'))
  await waitFor(() => expect(calls.some((c) => c.endsWith('/process'))).toBe(true))
  expect(puts).toEqual(['SYNTHETIC A', 'SYNTHETIC B']) // A is never re-sent over B
}, 10000)

test('after a failed autosave and a failed upload, Generate retries the on-screen text and closing warns', async () => {
  const ready: Meeting = { ...baseMeeting, state: 'transcript_added', coverage: null }
  const calls = mockApi(ready, (method, url) => (method === 'PUT' && url.endsWith('/transcript') ? { status: 400, body: { detail: 'SYNTHETIC refused' } } : url.endsWith('/transcript/upload') ? { status: 400, body: { detail: 'SYNTHETIC upload refused' } } : undefined))
  fireEvent.change(await screen.findByTestId('transcript'), { target: { value: 'SYNTHETIC typed' } })
  await waitFor(() => expect(screen.getByText('Transcript not saved: SYNTHETIC refused')).toBeInTheDocument(), { timeout: 3000 })
  fireEvent.change(document.querySelector('input[type=file][accept^=".txt"]') as HTMLInputElement, { target: { files: [new File(['SYNTHETIC uploaded'], 't.txt', { type: 'text/plain' })] } })
  expect(await screen.findByText('SYNTHETIC upload refused')).toBeInTheDocument()
  const unload = () => { const ev = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(ev); return ev.defaultPrevented }
  expect(unload()).toBe(true)
  const puts = () => calls.filter((c) => c.startsWith('PUT') && c.endsWith('/transcript')).length
  await waitFor(() => expect(screen.getByTestId('generate')).toBeEnabled())
  fireEvent.click(screen.getByTestId('generate'))
  await waitFor(() => expect(puts()).toBe(2)) // the unsaved text is retried, not silently dropped
  expect(await screen.findByText('Transcript not saved: SYNTHETIC refused')).toBeInTheDocument()
  expect(calls.some((c) => c.endsWith('/process'))).toBe(false)
})
