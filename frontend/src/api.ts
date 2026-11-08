// Thin fetch wrapper. The API token arrives once in the launch URL fragment (#token=...) and is kept
// in sessionStorage so it never appears in server logs or referrers.
import type { WorkspaceId } from './types'

const TOKEN_KEY = 'afn_token'

export function captureToken(): void {
  const m = window.location.hash.match(/token=([A-Za-z0-9_-]+)/)
  if (m) {
    sessionStorage.setItem(TOKEN_KEY, m[1])
    history.replaceState(null, '', window.location.pathname + window.location.search)
  }
}

export function hasToken(): boolean {
  return !!sessionStorage.getItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function request<T>(method: string, path: string, body?: unknown, raw?: FormData): Promise<T> {
  const headers: Record<string, string> = { 'X-AFN-Token': sessionStorage.getItem(TOKEN_KEY) ?? '' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(path, { method, headers, body: raw ?? (body === undefined ? undefined : JSON.stringify(body)) })
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') ?? ''
  const data = ct.includes('json') ? await res.json() : await res.text()
  if (!res.ok) throw new ApiError(res.status, typeof data === 'string' ? data : (data.detail ?? JSON.stringify(data)))
  return data as T
}

export const api = {
  get: <T>(p: string) => request<T>('GET', p),
  post: <T>(p: string, b?: unknown) => request<T>('POST', p, b),
  put: <T>(p: string, b?: unknown) => request<T>('PUT', p, b),
  patch: <T>(p: string, b?: unknown) => request<T>('PATCH', p, b),
  del: <T>(p: string) => request<T>('DELETE', p),
  upload: <T>(p: string, file: File) => { const fd = new FormData(); fd.append('file', file); return request<T>('POST', p, undefined, fd) },
  w: (ws: WorkspaceId, p: string) => `/api/w/${ws}${p}`,
}

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return 'Not authorised: open the app from the URL printed when you launched it (it contains the access token).'
    if (e.status === 502) return `Local model error: ${e.message}`
    return e.message
  }
  return e instanceof Error ? e.message : String(e)
}
