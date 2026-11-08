// Pure tree operations for the agenda editor. Ids are preserved; new nodes get fresh ids.
import type { AgendaNode } from './types'

export function newNode(text = ''): AgendaNode {
  return { id: 'n' + Math.random().toString(16).slice(2, 10), text, kind: 'topic', children: [] }
}

export interface Flat { node: AgendaNode; depth: number; parentId: string | null; index: number }

export function flatten(tree: AgendaNode[], depth = 0, parentId: string | null = null): Flat[] {
  return tree.flatMap((node, index) => [{ node, depth, parentId, index }, ...flatten(node.children, depth + 1, node.id)])
}

function clone(tree: AgendaNode[]): AgendaNode[] {
  return tree.map((n) => ({ ...n, children: clone(n.children) }))
}

function findParentList(tree: AgendaNode[], id: string): { list: AgendaNode[]; index: number } | null {
  const i = tree.findIndex((n) => n.id === id)
  if (i !== -1) return { list: tree, index: i }
  for (const n of tree) {
    const r = findParentList(n.children, id)
    if (r) return r
  }
  return null
}

function fixKinds(tree: AgendaNode[]): AgendaNode[] {
  for (const n of tree) {
    n.kind = n.children.length ? 'group' : 'topic'
    fixKinds(n.children)
  }
  return tree
}

export function updateText(tree: AgendaNode[], id: string, text: string): AgendaNode[] {
  const t = clone(tree)
  const r = findParentList(t, id)
  if (r) r.list[r.index].text = text
  return t
}

export function remove(tree: AgendaNode[], id: string): AgendaNode[] {
  const t = clone(tree)
  const r = findParentList(t, id)
  if (r) {
    const [gone] = r.list.splice(r.index, 1)
    r.list.splice(r.index, 0, ...gone.children) // children are promoted, never silently lost
  }
  return fixKinds(t)
}

export function insertAfter(tree: AgendaNode[], id: string | null, node = newNode()): AgendaNode[] {
  const t = clone(tree)
  if (id === null) { t.push(node); return fixKinds(t) }
  const r = findParentList(t, id)
  if (r) r.list.splice(r.index + 1, 0, node)
  return fixKinds(t)
}

export function move(tree: AgendaNode[], id: string, dir: -1 | 1): AgendaNode[] {
  const t = clone(tree)
  const r = findParentList(t, id)
  if (!r) return t
  const j = r.index + dir
  if (j < 0 || j >= r.list.length) return t
  ;[r.list[r.index], r.list[j]] = [r.list[j], r.list[r.index]]
  return t
}

export function indent(tree: AgendaNode[], id: string): AgendaNode[] {
  const t = clone(tree)
  const r = findParentList(t, id)
  if (!r || r.index === 0) return t
  const [node] = r.list.splice(r.index, 1)
  r.list[r.index - 1].children.push(node)
  return fixKinds(t)
}

export function outdent(tree: AgendaNode[], id: string): AgendaNode[] {
  const t = clone(tree)
  const flat = flatten(t)
  const me = flat.find((f) => f.node.id === id)
  if (!me || me.parentId === null) return t
  const parentLoc = findParentList(t, me.parentId)!
  const parent = parentLoc.list[parentLoc.index]
  const [node] = parent.children.splice(me.index, 1)
  // Following siblings become children of the outdented node, keeping document order.
  node.children.push(...parent.children.splice(me.index))
  parentLoc.list.splice(parentLoc.index + 1, 0, node)
  return fixKinds(t)
}

export function toText(tree: AgendaNode[]): string {
  return flatten(tree).map((f) => '  '.repeat(f.depth) + f.node.text).join('\n')
}
