import { useEffect, useMemo } from 'react'
import {
  Background,
  Handle,
  Position,
  ReactFlow,
  useReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { Eye, EyeOff } from 'lucide-react'
import {
  activePath,
  staleOnBranch,
  useForklore,
} from '@/lib/store'
import { layoutTree, truncate } from '@/lib/treeLayout'

interface FLNodeData {
  label: string
  onPath: boolean
  isCursor: boolean
  stale: boolean
  deleted: boolean
  hovered: boolean
}

function FLNode({ data }: NodeProps<FLNodeData>) {
  const cls = [
    'fl-node',
    data.onPath ? 'on-path' : '',
    data.isCursor ? 'is-cursor' : '',
    data.stale ? 'is-stale' : '',
    data.deleted ? 'is-deleted' : '',
    data.hovered ? 'is-hovered' : '',
  ].join(' ')
  return (
    <div className={cls}>
      <Handle type="target" position={Position.Top} className="fl-handle" />
      <div className="fl-dot" />
      <div className="fl-label">{data.label}</div>
      <Handle type="source" position={Position.Bottom} className="fl-handle" />
    </div>
  )
}

const nodeTypes = { fl: FLNode }

function FitOnChange({ count }: { count: number }) {
  const { fitView } = useReactFlow()
  useEffect(() => {
    const t = setTimeout(() => fitView({ padding: 0.15, duration: 300 }), 30)
    return () => clearTimeout(t)
  }, [count, fitView])
  return null
}

function TreeCanvas() {
  const proj = useForklore((s) => s.proj)
  const cursor = useForklore((s) => s.cursor)
  const branch = useForklore((s) => s.branch)
  const hovered = useForklore((s) => s.hovered)
  const showDeleted = useForklore((s) => s.showDeleted)
  const setCursor = useForklore((s) => s.setCursor)
  const setHovered = useForklore((s) => s.setHovered)

  const { nodes, edges, count } = useMemo(() => {
    if (!proj) return { nodes: [] as Node[], edges: [] as Edge[], count: 0 }
    const path = cursor ? activePath(proj, cursor) : new Set<string>()
    const stale = staleOnBranch(proj, branch)
    const { positions, edges: es } = layoutTree(proj, path, showDeleted)

    const ns: Node[] = []
    for (const [id, pos] of positions) {
      const n = proj.nodes[id]
      ns.push({
        id,
        type: 'fl',
        position: pos,
        draggable: false,
        data: {
          label: truncate(n.content, 42),
          onPath: path.has(id),
          isCursor: id === cursor,
          stale: stale.has(id),
          deleted: n.tombstoned,
          hovered: id === hovered,
        } satisfies FLNodeData,
      })
    }
    const edgeObjs: Edge[] = es.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      className: e.active ? 'fl-edge active' : 'fl-edge',
    }))
    return { nodes: ns, edges: edgeObjs, count: ns.length }
  }, [proj, cursor, branch, hovered, showDeleted])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesConnectable={false}
      fitView
      minZoom={0.15}
      maxZoom={1.6}
      onNodeClick={(_, n) => void setCursor(n.id)}
      onNodeMouseEnter={(_, n) => setHovered(n.id)}
      onNodeMouseLeave={() => setHovered(null)}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={26} size={1} className="fl-bg" />
      <FitOnChange count={count} />
    </ReactFlow>
  )
}

export default function TreePane() {
  const proj = useForklore((s) => s.proj)
  const showDeleted = useForklore((s) => s.showDeleted)
  const toggleDeleted = useForklore((s) => s.toggleDeleted)
  const count = proj ? Object.keys(proj.nodes).length : 0

  return (
    <aside className="relative min-h-0 border-r border-border flex flex-col">
      <div className="flex items-center justify-between px-5 py-3 border-b border-border">
        <span className="eyebrow">story tree</span>
        <div className="flex items-center gap-3">
          <span className="seqpill">{count} nodes</span>
          <button
            className="icon-btn"
            onClick={toggleDeleted}
            title={showDeleted ? 'hide tombstoned nodes' : 'show tombstoned nodes'}
          >
            {showDeleted ? <Eye className="size-4" /> : <EyeOff className="size-4" />}
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        <ReactFlowProvider>
          <TreeCanvas />
        </ReactFlowProvider>
      </div>
      <div className="px-5 py-2.5 border-t border-border">
        <p className="hint">click a node to move the cursor · hover syncs with the manuscript</p>
      </div>
    </aside>
  )
}
