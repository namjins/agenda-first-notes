import { flatten, indent, insertAfter, move, outdent, remove, updateText } from '../agendaTree'
import type { AgendaNode } from '../types'

export default function AgendaEditor({ tree, onChange, disabled }: { tree: AgendaNode[]; onChange: (t: AgendaNode[]) => void; disabled?: boolean }) {
  const rows = flatten(tree)
  return (
    <div className="space-y-1" data-testid="agenda-editor">
      {rows.map(({ node, depth }) => (
        <div key={node.id} className="flex items-center gap-1" style={{ paddingLeft: depth * 20 }}>
          <span className="muted w-4 text-center text-xs" aria-hidden>{node.kind === 'group' ? '▾' : '•'}</span>
          <input className="input" value={node.text} disabled={disabled} aria-label={`Agenda item at depth ${depth}`} onChange={(e) => onChange(updateText(tree, node.id, e.target.value))}
            onKeyDown={(e) => {
              if (disabled) return
              if (e.key === 'Enter') { e.preventDefault(); onChange(insertAfter(tree, node.id)) }
              else if (e.key === 'Tab') { e.preventDefault(); onChange(e.shiftKey ? outdent(tree, node.id) : indent(tree, node.id)) }
              else if (e.altKey && e.key === 'ArrowUp') { e.preventDefault(); onChange(move(tree, node.id, -1)) }
              else if (e.altKey && e.key === 'ArrowDown') { e.preventDefault(); onChange(move(tree, node.id, 1)) }
              else if (e.key === 'Backspace' && node.text === '') { e.preventDefault(); onChange(remove(tree, node.id)) }
            }} />
          {!disabled && (
            <div className="flex shrink-0 gap-0.5 text-xs">
              <button className="btn px-1.5" title="Move up (Alt+↑)" onClick={() => onChange(move(tree, node.id, -1))} aria-label="Move up">↑</button>
              <button className="btn px-1.5" title="Move down (Alt+↓)" onClick={() => onChange(move(tree, node.id, 1))} aria-label="Move down">↓</button>
              <button className="btn px-1.5" title="Outdent (Shift+Tab)" onClick={() => onChange(outdent(tree, node.id))} aria-label="Outdent">←</button>
              <button className="btn px-1.5" title="Indent (Tab)" onClick={() => onChange(indent(tree, node.id))} aria-label="Indent">→</button>
              <button className="btn px-1.5" title="Add item below (Enter)" onClick={() => onChange(insertAfter(tree, node.id))} aria-label="Add below">+</button>
              <button className="btn btn-danger px-1.5" title="Remove (children are kept)" onClick={() => onChange(remove(tree, node.id))} aria-label="Remove">×</button>
            </div>
          )}
        </div>
      ))}
      {!disabled && <button className="btn mt-1" onClick={() => onChange(insertAfter(tree, null))}>+ Add top-level item</button>}
      {rows.length === 0 && <p className="muted text-sm">No agenda items yet.</p>}
    </div>
  )
}
