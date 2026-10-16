/** Layered tree layout for the constellation pane: depth on the y axis,
 *  leaves distributed evenly on x, parents centered over their children. */
import type { Projection } from '@/types'
import { isHidden } from './store'

export interface LayoutResult {
  positions: Map<string, { x: number; y: number }>
  edges: { id: string; source: string; target: string; active: boolean }[]
  visibleIds: Set<string>
}

export function layoutTree(
  proj: Projection,
  path: Set<string>,
  showDeleted: boolean,
): LayoutResult {
  const positions = new Map<string, { x: number; y: number }>()
  const edges: LayoutResult['edges'] = []
  const visibleIds = new Set<string>()
  let leafCount = 0

  const visible = (id: string) => {
    const n = proj.nodes[id]
    if (!n || n.scope !== 'story') return false
    return showDeleted || !isHidden(proj, id)
  }

  function dfs(id: string, depth: number): number {
    const kids = proj.nodes[id].children.filter(visible)
    let myX: number
    if (kids.length === 0) {
      myX = leafCount++
    } else {
      const xs = kids.map((k) => dfs(k, depth + 1))
      myX = (xs[0] + xs[xs.length - 1]) / 2
    }
    positions.set(id, { x: myX * 120, y: depth * 92 })
    visibleIds.add(id)
    for (const k of kids) {
      edges.push({
        id: `${id}-${k}`,
        source: id,
        target: k,
        active: path.has(id) && path.has(k),
      })
    }
    return myX
  }

  if (proj.root && visible(proj.root)) dfs(proj.root, 0)
  return { positions, edges, visibleIds }
}

export function truncate(s: string, n: number): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length <= n ? t : t.slice(0, n - 1) + '…'
}
