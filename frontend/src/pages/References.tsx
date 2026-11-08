import { useState } from 'react'
import { api } from '../api'
import { useWs } from '../ws'
import type { ReferenceDoc } from '../types'
import { ErrorBox, Section, useAsync } from '../components/ui'

export default function References() {
  const { ws, wsName } = useWs()
  const docs = useAsync(() => api.get<ReferenceDoc[]>(api.w(ws, '/references')), [ws])
  const [err, setErr] = useState<unknown>(null)
  const [preview, setPreview] = useState<{ filename: string; text: string } | null>(null)

  const upload = async (files: FileList | null) => {
    if (!files) return
    for (const f of Array.from(files)) { try { await api.upload(api.w(ws, '/references'), f) } catch (e) { setErr(e) } }
    docs.reload()
  }

  return (
    <div className="max-w-4xl">
      <h1 className="mb-1 text-xl font-semibold">Reference Documents</h1>
      <p className="muted mb-3 text-sm">Optional, {wsName}-only: formatting instructions, public procedures, terminology, glossaries, templates. Processed locally. Reference material is never treated as evidence of what happened in a meeting; glossary matches only produce suggestions you confirm.</p>
      <ErrorBox error={err ?? docs.error} />
      <Section title="Upload (TXT, Markdown, PDF, DOCX)">
        <label className="btn btn-primary cursor-pointer">Choose files<input type="file" multiple accept=".txt,.md,.markdown,.pdf,.docx" className="hidden" onChange={(e) => upload(e.target.files)} /></label>
        <div className="muted mt-2 rounded-md border border-dashed p-6 text-center text-sm" style={{ borderColor: 'var(--line)' }} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files) }}>…or drop files here</div>
        <p className="muted mt-2 text-xs">Do not upload membership records, LCR exports or other restricted data. This application is not affiliated with or approved by any church.</p>
      </Section>
      <Section title="Documents">
        <table className="w-full text-sm"><thead className="muted text-left text-xs"><tr><th>File</th><th>Type</th><th>Chars</th><th>Glossary</th><th></th></tr></thead>
          <tbody>{(docs.data ?? []).map((d) => <tr key={d.id} className="border-t" style={{ borderColor: 'var(--line)' }}>
            <td className="py-1.5"><button className="underline" onClick={async () => setPreview(await api.get(api.w(ws, `/references/${d.id}`)))}>{d.filename}</button></td><td>{d.kind}</td><td>{d.length.toLocaleString()}</td>
            <td><input type="checkbox" checked={d.use_as_glossary} aria-label="Use as glossary" onChange={async (e) => { await api.post(api.w(ws, `/references/${d.id}/glossary`), { use_as_glossary: e.target.checked }); docs.reload() }} /></td>
            <td className="text-right"><button className="btn btn-danger" onClick={async () => { if (confirm(`Delete ${d.filename}?`)) { await api.del(api.w(ws, `/references/${d.id}`)); docs.reload() } }}>Delete</button></td></tr>)}</tbody></table>
        {docs.data?.length === 0 && <p className="muted text-sm">No reference documents.</p>}
      </Section>
      {preview && <Section title={preview.filename} right={<button className="btn" onClick={() => setPreview(null)}>Close</button>}><pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{preview.text}</pre></Section>}
    </div>
  )
}
