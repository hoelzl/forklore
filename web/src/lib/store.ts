/** Client state: a thin projection of the server log plus session state
 *  (cursor, undo stack, streaming buffer). All mutations go through the
 *  server; we never reimplement fold/materialize here. */
import { create } from 'zustand'
import { api, ssePost } from '@/lib/api'
import type { BranchInfo, BranchMsg, Projection, Settings, TNode } from '@/types'

// -- tree helpers (pure, operate on the fetched projection) ---------------

export function isHidden(proj: Projection, id: string): boolean {
  let cur: string | null = id
  while (cur) {
    const n: TNode | undefined = proj.nodes[cur]
    if (!n) return false
    if (n.tombstoned) return true
    cur = n.parent
  }
  return false
}

export function visibleChildren(proj: Projection, id: string): string[] {
  const n = proj.nodes[id]
  if (!n) return []
  return n.children.filter(
    (c) => proj.nodes[c] && proj.nodes[c].scope === 'story' && !proj.nodes[c].tombstoned,
  )
}

/** Deepest visible story descendant, preferring the most recently
 *  created child at each fork — i.e. "the latest swipe". */
export function deepestLeaf(proj: Projection, id: string): string {
  let cur = id
  for (;;) {
    const kids = visibleChildren(proj, cur)
    if (kids.length === 0) return cur
    cur = kids.reduce((a, b) =>
      proj.nodes[a].created_seq >= proj.nodes[b].created_seq ? a : b,
    )
  }
}

/** Node ids on the path root..cursor (the active storyline). */
export function activePath(proj: Projection, cursor: string): Set<string> {
  const out = new Set<string>()
  let cur: string | null = cursor
  while (cur) {
    out.add(cur)
    cur = proj.nodes[cur]?.parent ?? null
  }
  return out
}

/** Client-side stale computation mirroring core.materialize.stale:
 *  an ancestor revised after this node was created, unless the branch
 *  is pinned at that edge. */
export function staleOnBranch(proj: Projection, branch: BranchMsg[]): Set<string> {
  const out = new Set<string>()
  for (let i = 1; i < branch.length; i++) {
    const n = proj.nodes[branch[i].node]
    if (!n) continue
    for (let j = 0; j < i; j++) {
      const anc = proj.nodes[branch[j].node]
      if (!anc || anc.last_revise_seq <= n.created_seq) continue
      const via = branch[j + 1].node
      if (proj.pins[`${anc.id}|${via}`] !== undefined) continue
      out.add(n.id)
      break
    }
  }
  return out
}

// -- store ------------------------------------------------------------------

interface ForkloreState {
  proj: Projection | null
  cursor: string | null
  branch: BranchMsg[]
  strategies: string[]
  strategy: string | null
  streaming: { active: boolean; text: string }
  undoStack: string[]
  hovered: string | null
  showDeleted: boolean
  workshop: { nodeId: string; tx: string; artifacts: TNode[] } | null
  branches: BranchInfo[]
  compare: { a: string; b: string } | null
  play: boolean
  settings: Settings | null
  error: string | null
  initialized: boolean

  init: () => Promise<void>
  refresh: () => Promise<void>
  setCursor: (id: string) => Promise<void>
  swipe: (index: number, dir: -1 | 1) => Promise<void>
  writeMessage: (content: string) => Promise<void>
  generate: (leaf?: string, params?: Record<string, unknown>) => Promise<void>
  regenerate: (nodeId: string) => Promise<void>
  reviseAll: (nodeId: string, content: string) => Promise<void>
  divergeHere: (index: number, content: string) => Promise<void>
  removeNode: (nodeId: string) => Promise<void>
  undo: () => Promise<void>
  setHovered: (id: string | null) => void
  setStrategy: (id: string) => void
  toggleDeleted: () => void
  clearError: () => void
  loadSettings: () => Promise<void>
  saveSettings: (patch: Partial<Settings>) => Promise<boolean>
  openWorkshop: (nodeId: string) => Promise<void>
  closeWorkshop: () => void
  loadBranches: () => Promise<void>
  nameBranch: (name: string, node: string) => Promise<boolean>
  unnameBranch: (name: string) => Promise<void>
  pruneBranch: (name: string) => Promise<void>
  setCompare: (c: { a: string; b: string } | null) => void
  setPlay: (on: boolean) => void
}

let eventsource: EventSource | null = null
let refreshTimer: ReturnType<typeof setTimeout> | null = null

export const useForklore = create<ForkloreState>()((set, get) => {
  const pushUndo = (tx: string) =>
    set((s) => ({ undoStack: [...s.undoStack, tx] }))

  const scheduleRefresh = () => {
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = setTimeout(() => void get().refresh(), 120)
  }

  return {
    proj: null,
    cursor: null,
    branch: [],
    strategies: [],
    strategy: null,
    streaming: { active: false, text: '' },
    undoStack: [],
    hovered: null,
    showDeleted: false,
    workshop: null,
    branches: [],
    compare: null,
    play: false,
    settings: null,
    error: null,
    initialized: false,

    async init() {
      if (get().initialized) return
      set({ initialized: true })
      try {
        const strategies: string[] = await api.get('/strategies')
        const proj: Projection = await api.get('/state')
        const cursor = proj.root ? deepestLeaf(proj, proj.root) : null
        set({ strategies, strategy: strategies[0] ?? null, proj, cursor })
        if (cursor) {
          const b = await api.get(`/branch/${cursor}`)
          set({ branch: b.messages })
        }
        await get().loadBranches()
        eventsource?.close()
        eventsource = new EventSource('/events/stream?after=0')
        eventsource.addEventListener('event', scheduleRefresh)
        eventsource.onopen = scheduleRefresh
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async refresh() {
      const { cursor, workshop } = get()
      try {
        const proj: Projection = await api.get('/state')
        let cur = cursor
        if (!cur || !proj.nodes[cur]) {
          cur = proj.root ? deepestLeaf(proj, proj.root) : null
        }
        const b = cur ? await api.get(`/branch/${cur}`) : { messages: [] }
        set({ proj, cursor: cur, branch: b.messages })
        if (workshop) await get().openWorkshop(workshop.nodeId)
        await get().loadBranches()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async setCursor(id) {
      set({ cursor: id })
      const b = await api.get(`/branch/${id}`)
      set({ branch: b.messages })
    },

    async swipe(index, dir) {
      const { proj, branch } = get()
      if (!proj) return
      const msg = branch[index]
      const node = proj.nodes[msg.node]
      if (!node?.parent) return
      const sibs = visibleChildren(proj, node.parent)
      const i = sibs.indexOf(msg.node)
      const j = i + dir
      if (j < 0 || j >= sibs.length) return
      await get().setCursor(deepestLeaf(proj, sibs[j]))
    },

    async writeMessage(content) {
      const { cursor } = get()
      try {
        const ev = await api.post('/nodes', {
          parent: cursor,
          author: 'you',
          content,
        })
        pushUndo(ev.tx)
        set({ cursor: ev.payload.node })
        await get().refresh()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async generate(leaf, params) {
      const { cursor, strategy, streaming } = get()
      const target = leaf ?? cursor
      if (!target || !strategy || streaming.active) return
      set({ streaming: { active: true, text: '' }, error: null })
      let committed: any = null
      try {
        for await (const ev of ssePost('/generate', {
          leaf: target,
          strategy,
          author: 'character',
          ...(params && Object.keys(params).length ? { params } : {}),
        })) {
          if (ev.event === 'token') {
            set((s) => ({
              streaming: { active: true, text: s.streaming.text + ev.data.delta },
            }))
          } else if (ev.event === 'committed') {
            committed = ev.data.event
          } else if (ev.event === 'error') {
            set({ error: ev.data.message })
          }
        }
      } catch (e) {
        set({ error: String(e) })
      }
      set({ streaming: { active: false, text: '' } })
      if (committed) {
        pushUndo(committed.tx)
        set({ cursor: committed.payload.node })
        await get().refresh()
      }
    },

    async regenerate(nodeId) {
      const { proj } = get()
      const parent = proj?.nodes[nodeId]?.parent
      if (parent) await get().generate(parent)
    },

    async reviseAll(nodeId, content) {
      try {
        const ev = await api.post(`/nodes/${nodeId}/revise`, { content })
        pushUndo(ev.tx)
        await get().refresh()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async divergeHere(index, content) {
      const { branch, cursor } = get()
      const msg = branch[index]
      const next = branch[index + 1]
      try {
        if (next) {
          // internal node: variant + graft the current continuation
          const r = await api.post(`/nodes/${msg.node}/diverge`, {
            content,
            move: next.node,
          })
          pushUndo(r.variant.tx)
        } else {
          // leaf: plain variant, then follow it
          const ev = await api.post(`/nodes/${msg.node}/variant`, { content })
          pushUndo(ev.tx)
          if (cursor === msg.node) set({ cursor: ev.payload.node })
        }
        await get().refresh()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async removeNode(nodeId) {
      const { proj, cursor } = get()
      if (!proj) return
      try {
        const ev = await api.post(`/nodes/${nodeId}/tombstone`)
        pushUndo(ev.tx)
        // if the cursor was inside the removed subtree, step back
        let cur: string | null = cursor
        while (cur) {
          if (cur === nodeId) {
            set({ cursor: proj.nodes[nodeId].parent })
            break
          }
          cur = proj.nodes[cur]?.parent ?? null
        }
        await get().refresh()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async undo() {
      const stack = [...get().undoStack]
      const tx = stack.pop()
      if (!tx) return
      set({ undoStack: stack })
      try {
        await api.post(`/undo/${tx}`)
        await get().refresh()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    setHovered: (id) => set({ hovered: id }),
    setStrategy: (id) => set({ strategy: id }),
    toggleDeleted: () => set((s) => ({ showDeleted: !s.showDeleted })),
    clearError: () => set({ error: null }),

    async loadSettings() {
      try {
        set({ settings: await api.get('/settings') })
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async saveSettings(patch) {
      try {
        set({ settings: await api.put('/settings', patch), error: null })
        return true
      } catch (e) {
        set({ error: String(e) })
        return false
      }
    },

    async openWorkshop(nodeId) {
      try {
        const r = await api.get(`/workspace/${nodeId}`)
        set({ workshop: { nodeId, tx: r.tx, artifacts: r.artifacts } })
      } catch (e) {
        set({ error: String(e) })
      }
    },

    closeWorkshop: () => set({ workshop: null }),

    async loadBranches() {
      try {
        set({ branches: await api.get('/branches') })
      } catch { /* branches are cosmetic; don't noise the error pill */ }
    },

    async nameBranch(name, node) {
      try {
        const ev = await api.post('/branches', { name, node })
        pushUndo(ev.tx)
        await get().refresh()
        return true
      } catch (e) {
        set({ error: String(e) })
        return false
      }
    },

    async unnameBranch(name) {
      try {
        const ev = await api.delete(`/branches/${encodeURIComponent(name)}`)
        pushUndo(ev.tx)
        await get().refresh()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    async pruneBranch(name) {
      try {
        const events = await api.post(`/branches/${encodeURIComponent(name)}/prune`)
        if (events?.[0]?.tx) pushUndo(events[0].tx)
        await get().refresh()
      } catch (e) {
        set({ error: String(e) })
      }
    },

    setCompare: (c) => set({ compare: c }),
    setPlay: (on) => set((s) => ({ play: on, compare: on ? null : s.compare })),
  }
})

export type { TNode }
