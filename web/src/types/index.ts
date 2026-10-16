/** Shared types mirroring the forklore server projections. */

export interface GenMeta {
  strategy: string
  model: string
  params?: Record<string, unknown>
  watermark: number
  context_hash: string
}

export interface TNode {
  id: string
  parent: string | null
  author: string
  scope: 'story' | 'workspace'
  created_seq: number
  version: number
  content: string
  versions: string[]
  tombstoned: boolean
  meta: Record<string, unknown>
  children: string[]
  last_revise_seq: number
  generation: GenMeta | null
}

export interface Projection {
  head: number
  root: string | null
  nodes: Record<string, TNode>
  /** keys are "node|via_child" */
  pins: Record<string, number>
}

export interface BranchMsg {
  node: string
  author: string
  content: string
  version: number
  pinned: boolean
  scope: string
}

export interface BranchResp {
  messages: BranchMsg[]
  stale: string[]
}

export interface EventJson {
  seq: number
  id: string
  tx: string
  actor: string
  kind: string
  payload: any
}

export interface SSEvent {
  event: string
  data: any
}
