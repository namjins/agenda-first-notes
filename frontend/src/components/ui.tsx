import { useEffect, useState, type ReactNode } from 'react'
import { describeError } from '../api'

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  return <div role="alert" className="tag tag-bad block whitespace-pre-wrap rounded-md p-2 text-sm">{describeError(error)}</div>
}

export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="panel mb-4 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let live = true
    setLoading(true)
    fn().then((d) => { if (live) { setData(d); setError(null) } }).catch((e) => { if (live) setError(e) }).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData }
}

// Single-key shortcuts must not fire while the user is typing or choosing (select type-ahead uses letters too).
export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  return !!el && (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable)
}

export function StateTag({ state }: { state: string }) {
  const cls = state === 'approved' ? 'tag-ok' : state === 'review_required' || state === 'failed' ? 'tag-bad' : state === 'ready_for_approval' ? 'tag-warn' : 'tag-muted'
  return <span className={`tag ${cls}`}>{state.replaceAll('_', ' ')}</span>
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return 'Undated'
  const d = new Date(iso + (iso.length === 10 ? 'T00:00:00' : ''))
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}

export async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true } catch { return false }
}
