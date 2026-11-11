// Member-list name suggestions UI (SYNTHETIC data only; every name is invented).
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import NameSuggestions, { AgendaNameCheck } from './components/NameSuggestions'
import ReviewPanel from './components/ReviewPanel'
import Members from './pages/Members'
import { WsProvider } from './ws'
import type { AgendaNode, NameSuggestions as Data, Note, ReviewView } from './types'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function mockFetch(reply: (method: string, url: string, body: unknown) => unknown) {
  const calls: { method: string; url: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    calls.push({ method, url, body })
    return new Response(JSON.stringify(reply(method, url, body) ?? {}), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
  return calls
}

const suggestion: Data = {
  member_count: 2,
  answers: [],
  needs_regeneration: false,
  suggestions: [{
    heard: 'bob boss', start: 10, count: 2, quotes: ['SYNTHETIC Primary: bob boss can teach the sunbeams.', 'SYNTHETIC later: bob boss left.'], agenda_item: 'Organizations › Primary', orgs: ['Primary'],
    candidates: [{ member_id: 'mem1', full_name: 'Ross, Robert James', name: 'Robert James Ross', preferred: 'Robert Ross', callings: ['Primary Teacher, sustained 5 Jan 2025'], reason: '"bob" is a common nickname for Robert', score: 0.95 }],
  }],
}
const confirmed = { id: 'c1', heard: 'bob boss', name: 'Robert James Ross', preferred: 'Robert Ross', status: 'confirmed' as const, member_id: 'mem1', in_transcript: true }

test('a suggestion card explains the candidate and every mention it applies to; confirming asks for regeneration', async () => {
  let answered = false
  const calls = mockFetch((method) => {
    if (method === 'POST') { answered = true; return {} }
    return answered ? { ...suggestion, suggestions: [], needs_regeneration: true, answers: [confirmed] } : suggestion
  })
  const onChange = vi.fn()
  render(<NameSuggestions ws="ward_council" meetingId="m1" version={1} readOnly={false} onChange={onChange} />)
  const card = await screen.findByTestId('name-suggestion')
  for (const t of ['“bob boss”', '2 mentions', 'SYNTHETIC Primary: bob boss can teach the sunbeams.', 'SYNTHETIC later: bob boss left.', 'A confirmation applies to all 2 mentions in this meeting.', 'Agenda item: Organizations › Primary', 'Robert Ross', '(Robert James Ross)', '· Primary Teacher, sustained 5 Jan 2025', 'Why: "bob" is a common nickname for Robert']) {
    expect(card).toHaveTextContent(t)
  }
  expect(screen.queryByText(/Regenerate the notes/)).toBeNull() // nothing changed since the last generation
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Robert Ross for “bob boss”' }))
  await waitFor(() => expect(screen.getByText(/Regenerate the notes to apply them./)).toBeInTheDocument())
  expect(calls.find((c) => c.method === 'POST')).toMatchObject({ url: '/api/w/ward_council/meetings/m1/name-confirmations', body: { heard: 'bob boss', member_id: 'mem1', status: 'confirmed' } })
  expect(screen.getByText('Confirmed by you')).toBeInTheDocument()
  expect(onChange).toHaveBeenCalled() // the review panel refreshes
})

test('answers stay visible without a member list, a stale one is flagged, and removing refreshes the review', async () => {
  const calls = mockFetch(() => ({ member_count: 0, suggestions: [], needs_regeneration: false, answers: [{ ...confirmed, in_transcript: false }] }))
  const onChange = vi.fn()
  render(<NameSuggestions ws="ward_council" meetingId="m1" version={1} readOnly={false} onChange={onChange} />)
  expect(await screen.findByText('“bob boss” → Robert James Ross')).toBeInTheDocument()
  expect(screen.getByText('No longer in the transcript')).toBeInTheDocument()
  expect(screen.getByText(/No member list loaded/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Remove Robert James Ross for “bob boss”' }))
  await waitFor(() => expect(onChange).toHaveBeenCalled())
  expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/name-confirmations/c1'))).toBe(true)
})

test('Members page states the list stays local, lists members and deletes the list', async () => {
  let rows = [{ preferred_name: 'Ross, Robert', full_name: 'Ross, Robert James', spouse_name: 'Ross, Mary', callings: [{ calling: 'Primary Teacher', sustained: '5 Jan 2025' }] }]
  const calls = mockFetch((method) => { if (method === 'DELETE') { rows = []; return { deleted: 1 } } return { count: rows.length, members: rows } })
  const ask = vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<WsProvider><Members /></WsProvider>)
  expect(await screen.findByText('Ross, Robert James')).toBeInTheDocument()
  expect(screen.getByText(/used by both Ward Council and Bishopric\. It stays on this computer/)).toBeInTheDocument()
  expect(screen.getByText('1 member(s)')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Delete member list' }))
  await waitFor(() => expect(screen.getByText('No member list loaded.')).toBeInTheDocument())
  expect(ask.mock.calls[0][0]).toMatch(/both Ward Council and Bishopric.*Names you already confirmed for meetings stay with those meetings/s)
  expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/members')).toBe(true)
})

test('a note whose name the user confirmed is never labelled Source verified', () => {
  const note: Note = { id: 'A', meeting_id: 'm1', agenda_node_id: 'n1', position: 0, text: 'Robert Ross can teach.', category: 'discussion', uncertainty: 'low', follow_up: false, owner: null, calling_event: null, status: 'verified', origin: 'model', evidence: [], stale: false, deleted: false, name_confirmed: true }
  const view = { meeting: { id: 'm1', agenda: { tree: [{ id: 'n1', text: 'Primary', kind: 'topic', children: [] }] }, carry_forward: [] }, notes: [note], findings: [], coverage: null, markdown: '', undiscussed_follow_ups: [] } as unknown as ReviewView
  render(<ReviewPanel ws="ward_council" view={view} transcript="" onChange={() => {}} readOnly />)
  expect(screen.getByText('Name confirmed by you')).toBeInTheDocument()
  expect(screen.queryByText('Source verified')).toBeNull()
})

test('agenda step: a close member spelling is offered; Fix spelling edits only the draft heading, Dismiss hides it', async () => {
  const tree: AgendaNode[] = [{ id: 'g', text: 'Callings', kind: 'group', children: [{ id: 'a', text: "Extend Sindy Jones's calling", kind: 'topic', children: [] }, { id: 'b', text: 'Sheryl Lomy - Relief Society', kind: 'topic', children: [] }] }]
  const reply = [
    { node_id: 'a', start: 7, end: 18, written: 'Sindy Jones', candidates: [{ member_id: 'mem1', name: 'Cindy Jones', callings: ['Primary Teacher'] }] },
    { node_id: 'b', start: 0, end: 11, written: 'Sheryl Lomy', candidates: [{ member_id: 'mem2', name: 'Cheryl Lomy', callings: [] }] },
  ]
  const calls = mockFetch(() => reply)
  const onChange = vi.fn()
  const { rerender } = render(<AgendaNameCheck ws="bishopric" meetingId="m1" tree={tree} onChange={onChange} />)
  expect(await screen.findByText('Agenda says “Sindy Jones” — member list has “Cindy Jones” (Primary Teacher). Fix spelling?')).toBeInTheDocument()
  expect(screen.getByText('Agenda says “Sheryl Lomy” — member list has “Cheryl Lomy”. Fix spelling?')).toBeInTheDocument()
  expect(calls[0]).toMatchObject({ method: 'POST', url: '/api/w/bishopric/meetings/m1/agenda/name-check', body: { tree } })
  fireEvent.click(screen.getByRole('button', { name: 'Change “Sindy Jones” to “Cindy Jones”' }))
  const edited = onChange.mock.calls[0][0] as AgendaNode[]
  expect(edited[0].children.map((c) => c.text)).toEqual(["Extend Cindy Jones's calling", 'Sheryl Lomy - Relief Society']) // only that span
  expect(tree[0].children[0].text).toBe("Extend Sindy Jones's calling") // a new tree, as from the editor
  rerender(<AgendaNameCheck ws="bishopric" meetingId="m1" tree={edited} onChange={onChange} />)
  expect(screen.queryByText(/Agenda says “Sindy Jones”/)).toBeNull() // the heading no longer says it
  fireEvent.click(screen.getByRole('button', { name: 'Keep “Sheryl Lomy”' }))
  expect(screen.queryByTestId('agenda-name-check')).toBeNull()
  expect(onChange).toHaveBeenCalledTimes(1) // dismissing changes nothing
})

test('agenda step: an emoji before the name (Python offsets count it once) still finds and fixes the name', async () => {
  const tree: AgendaNode[] = [{ id: 'a', text: "🎉 Extend Sindy Jones's calling", kind: 'topic', children: [] }]
  // SYNTHETIC: the server's offsets are code points, one less than the UTF-16 index here
  mockFetch(() => [{ node_id: 'a', start: 9, end: 20, written: 'Sindy Jones', candidates: [{ member_id: 'mem1', name: 'Cindy Jones', callings: [] }] }])
  const onChange = vi.fn()
  render(<AgendaNameCheck ws="bishopric" meetingId="m1" tree={tree} onChange={onChange} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Change “Sindy Jones” to “Cindy Jones”' }))
  expect((onChange.mock.calls[0][0] as AgendaNode[])[0].text).toBe("🎉 Extend Cindy Jones's calling")
})
