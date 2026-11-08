import { describe, expect, it } from 'vitest'
import { flatten, indent, insertAfter, move, outdent, remove, toText, updateText } from './agendaTree'
import type { AgendaNode } from './types'

const n = (id: string, text: string, children: AgendaNode[] = []): AgendaNode => ({ id, text, kind: children.length ? 'group' : 'topic', children })
const tree = () => [n('a', 'Opening'), n('b', 'Organizations', [n('c', 'Relief Society'), n('d', 'Elders Quorum')]), n('e', 'Closing')]

describe('agenda tree ops', () => {
  it('keeps ids and text verbatim through edits', () => {
    const t = updateText(tree(), 'c', 'Relief  Society!')
    expect(flatten(t).map((f) => f.node.id)).toEqual(['a', 'b', 'c', 'd', 'e'])
    expect(t[1].children[0].text).toBe('Relief  Society!')
  })
  it('indent / outdent / move preserve every node', () => {
    let t = indent(tree(), 'e') // Closing under Organizations
    expect(t[1].children.map((c) => c.id)).toEqual(['c', 'd', 'e'])
    t = outdent(t, 'c') // c out; d,e become c's children
    expect(t.map((x) => x.id)).toEqual(['a', 'b', 'c'])
    expect(t[2].children.map((c) => c.id)).toEqual(['d', 'e'])
    expect(t[1].kind).toBe('topic')
    t = move(t, 'a', 1)
    expect(t.map((x) => x.id)).toEqual(['b', 'a', 'c'])
    expect(flatten(t)).toHaveLength(5)
  })
  it('remove promotes children instead of deleting them', () => {
    const t = remove(tree(), 'b')
    expect(t.map((x) => x.id)).toEqual(['a', 'c', 'd', 'e'])
  })
  it('insertAfter and toText', () => {
    const t = insertAfter(tree(), 'a', { id: 'x', text: 'Ministering', kind: 'topic', children: [] })
    expect(toText(t)).toBe('Opening\nMinistering\nOrganizations\n  Relief Society\n  Elders Quorum\nClosing')
  })
})
