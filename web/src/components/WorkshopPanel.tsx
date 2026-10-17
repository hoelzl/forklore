/** Generation workshop: the workspace artifacts of one generation run —
 * rejected candidates, intermediate drafts, critiques — in execution
 * order. These nodes never appear in storylines, but they are never
 * discarded either: this panel is where the machinery shows its work. */
import { X } from 'lucide-react'
import { useForklore } from '@/lib/store'
import type { TNode } from '@/types'

function chips(a: TNode): { label: string; title: string }[] {
  const p = a.generation?.params ?? {}
  const out: { label: string; title: string }[] = []
  if (p.stage !== undefined) {
    out.push({
      label: String(p.stage) + (p.round ? ` ${String(p.round)}` : ''),
      title: `stage: ${String(p.stage)}${p.round ? ` · round ${String(p.round)}` : ''}`,
    })
  }
  if (p.score !== undefined) {
    out.push({
      label: `score ${String(p.score)}`,
      title: p.pool !== undefined
        ? `critic score ${String(p.score)}/10 · pool of ${String(p.pool)}`
        : `critic score ${String(p.score)}/10`,
    })
  }
  if (p.verdict !== undefined) {
    out.push({ label: String(p.verdict).toLowerCase(), title: `verdict: ${String(p.verdict)}` })
  }
  return out
}

export default function WorkshopPanel() {
  const workshop = useForklore((s) => s.workshop)
  const proj = useForklore((s) => s.proj)
  const closeWorkshop = useForklore((s) => s.closeWorkshop)
  if (!workshop) return null

  const source = proj?.nodes[workshop.nodeId]
  const gen = source?.generation

  return (
    <aside className="w-[360px] shrink-0 border-l border-border flex flex-col min-h-0 bg-background/60">
      <header className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-border">
        <div className="min-w-0 flex-1">
          <p className="eyebrow">generation workshop</p>
          {gen && (
            <p className="hint truncate" title={`model ${gen.model} · tx ${workshop.tx.slice(0, 8)}`}>
              {gen.strategy} · {gen.model}
            </p>
          )}
        </div>
        <button className="icon-btn" onClick={closeWorkshop} title="close">
          <X className="size-4" />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto fl-scroll px-4 py-4 space-y-4">
        {workshop.artifacts.length === 0 && (
          <p className="text-sm text-muted-foreground leading-relaxed">
            This run kept no workspace artifacts — the strategy went straight
            to a reply.
          </p>
        )}
        {workshop.artifacts.map((a) => (
          <article key={a.id} className={a.tombstoned ? 'opacity-50' : ''}>
            <header className="flex items-baseline gap-2 mb-1.5">
              <span className="seqpill">#{a.created_seq}</span>
              {chips(a).map((c) => (
                <span key={c.label} className="chip" title={c.title}>{c.label}</span>
              ))}
            </header>
            <p className="manuscript text-[15px] leading-relaxed">{a.content}</p>
          </article>
        ))}
      </div>

      <footer className="shrink-0 px-4 py-3 border-t border-border">
        <p className="hint leading-relaxed">
          workspace nodes never enter a storyline — but nothing the strategy
          produced is discarded.
        </p>
      </footer>
    </aside>
  )
}
