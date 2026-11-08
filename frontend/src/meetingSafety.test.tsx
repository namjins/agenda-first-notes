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
    const r = override(method, url) ?? { body: url.endsWith('/review') ? view(meeting) : url.endsWith('/jobs') ? [] : method === 'GET' && url.endsWith('/transcript') ? { text: 'SYNTHETIC' } : url.endsWith('/process') ? { id: 'j1', status: 'done', progress: 1, message: '' } : meeting }
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
  fireEvent.click(await screen.findByTestId('approve-agenda'))
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
  await act(async () => { resolve(new Response(new Blob(['x']), { status: 200 })) })
  expect(create).not.toHaveBeenCalled()
})
