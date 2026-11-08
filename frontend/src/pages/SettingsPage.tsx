import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ModelsResponse, Settings } from '../types'
import { ErrorBox, Section, useAsync } from '../components/ui'

export default function SettingsPage() {
  const settings = useAsync(() => api.get<Settings>('/api/settings'), [])
  const models = useAsync(() => api.get<ModelsResponse>('/api/models'), [])
  const [form, setForm] = useState<Settings | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const [test, setTest] = useState<string>('')
  const [paths, setPaths] = useState<string>('')
  useEffect(() => { if (settings.data) setForm(settings.data) }, [settings.data])
  useEffect(() => { api.get<{ ok: boolean; version: string }>('/api/health').then((h) => setPaths(`v${h.version}`)).catch(() => {}) }, [])
  if (!form) return <ErrorBox error={settings.error} />
  const m = form.models
  const set = (k: keyof Settings['models'], v: unknown) => setForm({ ...form, models: { ...m, [k]: v } })
  const names = models.data?.models.map((x) => x.name) ?? []
  const vision = models.data?.models.filter((x) => x.capabilities.includes('vision')).map((x) => x.name) ?? []
  const save = async () => { try { await api.put('/api/settings', form); setSaved(true); setErr(null); setTimeout(() => setSaved(false), 1500); models.reload() } catch (e) { setErr(e) } }
  const runTest = async (model: string | null) => { if (!model) return; setTest('Running synthetic test inference…'); try { const r = await api.post<{ seconds: number; ok: boolean; raw: string }>('/api/models/test', { model }); setTest(`${model}: ${r.ok ? 'OK' : 'unexpected output'} in ${r.seconds}s — ${r.raw}`) } catch (e) { setTest(String((e as Error).message)) } }
  const warn = (name: string | null) => { const info = models.data?.models.find((x) => x.name === name); if (!info) return null; const gb = info.size_bytes ? info.size_bytes / 1e9 : 0; return gb > 40 ? 'Large model: needs ~' + Math.round(gb * 1.2) + ' GB memory.' : null }

  return (
    <div className="max-w-3xl">
      <h1 className="mb-3 text-xl font-semibold">Settings</h1>
      <ErrorBox error={err} />
      <Section title="Ollama connection" right={<button className="btn" onClick={() => models.reload()}>Refresh</button>}>
        <label className="muted text-xs">Ollama host (local only)</label>
        <input className="input" value={m.ollama_host} onChange={(e) => set('ollama_host', e.target.value)} />
        <div className="mt-2 text-sm">{models.data?.connected ? <span className="tag tag-ok">Connected · {names.length} model(s)</span> : <span className="tag tag-bad">Not reachable</span>}</div>
        {models.data?.warnings.map((w) => <div key={w} className="tag tag-warn mt-1 block">{w}</div>)}
        <p className="muted mt-2 text-xs">Only loopback endpoints are accepted. The app cannot verify how an externally managed Ollama process is configured (for example, whether it is also listening on other interfaces); set <code>OLLAMA_HOST=127.0.0.1</code> for Ollama itself if you want to be sure. No cloud fallback exists.</p>
      </Section>
      <Section title="Models">
        {[['primary_model', 'Primary extraction model', names], ['reviewer_model', 'Independent reviewer model (optional)', names], ['vision_model', 'Vision model for agenda screenshots (optional)', vision]].map(([key, label, opts]) => (
          <div key={key as string} className="mb-3">
            <label className="muted text-xs">{label as string}</label>
            <div className="flex gap-2">
              <select className="input" value={(m[key as keyof typeof m] as string | null) ?? ''} onChange={(e) => set(key as keyof typeof m, e.target.value || null)}>
                <option value="">{key === 'primary_model' ? '— choose —' : key === 'reviewer_model' ? 'Same as primary' : 'None (use Tesseract if installed, or manual entry)'}</option>
                {(opts as string[]).map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <button className="btn" onClick={() => runTest(m[key as keyof typeof m] as string | null)} disabled={!m[key as keyof typeof m]}>Test</button>
            </div>
            {warn(m[key as keyof typeof m] as string | null) && <div className="tag tag-warn mt-1">{warn(m[key as keyof typeof m] as string | null)}</div>}
          </div>
        ))}
        {test && <div className="muted mt-1 text-xs">{test}</div>}
        <div className="muted text-xs">Image-to-text backends available: {(models.data?.ocr_backends ?? []).join(', ') || 'none (manual agenda entry still works)'}</div>
      </Section>
      <Section title="Inference parameters">
        <div className="grid gap-3 md:grid-cols-2">
          {([['context_length', 'Context length (tokens)'], ['max_output_tokens', 'Max output tokens'], ['temperature', 'Temperature'], ['max_review_rounds', 'Max automatic review rounds'], ['segment_chars', 'Transcript segment size (chars)'], ['segment_overlap_chars', 'Segment overlap (chars)']] as const).map(([k, label]) => (
            <label key={k} className="text-sm"><span className="muted block text-xs">{label}</span><input className="input" type="number" step={k === 'temperature' ? 0.05 : 1} value={m[k]} onChange={(e) => set(k, Number(e.target.value))} /></label>
          ))}
        </div>
        <p className="muted mt-2 text-xs">Models run sequentially to conserve memory. Lower-memory machines: choose a smaller model and reduce context length. Transcripts longer than one segment are processed in overlapping chunks; nothing is truncated.</p>
      </Section>
      <div className="flex items-center gap-3"><button className="btn btn-primary" onClick={save}>Save settings</button>{saved && <span className="tag tag-ok">Saved</span>}<span className="muted text-xs">{paths}</span></div>
    </div>
  )
}
