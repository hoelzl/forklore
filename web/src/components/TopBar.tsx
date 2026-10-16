import { Undo2 } from 'lucide-react'
import { useForklore } from '@/lib/store'
import { Button } from '@/components/ui/button'

export default function TopBar() {
  const head = useForklore((s) => s.proj?.head ?? 0)
  const strategies = useForklore((s) => s.strategies)
  const strategy = useForklore((s) => s.strategy)
  const setStrategy = useForklore((s) => s.setStrategy)
  const undoCount = useForklore((s) => s.undoStack.length)
  const undo = useForklore((s) => s.undo)
  const streaming = useForklore((s) => s.streaming.active)

  return (
    <header className="h-13 shrink-0 border-b border-border flex items-center gap-5 px-6 py-3">
      <div className="flex items-baseline gap-3">
        <span className="font-serif italic text-[22px] leading-none tracking-tight">
          Forklore
        </span>
        <span className="eyebrow hidden sm:inline">branching story workbench</span>
      </div>
      <div className="flex-1" />
      {strategy && (
        <select
          className="chip bg-transparent cursor-pointer hover:text-foreground transition-colors"
          title="generation strategy"
          value={strategy}
          onChange={(e) => setStrategy(e.target.value)}
        >
          {strategies.map((s) => (
            <option key={s} value={s} className="bg-popover">
              {s}
            </option>
          ))}
        </select>
      )}
      <span className="seqpill" title="log head — every state is reproducible up to a seq">
        head #{head}
      </span>
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        disabled={undoCount === 0 || streaming}
        onClick={() => void undo()}
        title="undo the last operation (non-destructive: appends a compensating event)"
      >
        <Undo2 className="size-3.5" />
        undo{undoCount > 0 ? ` · ${undoCount}` : ''}
      </Button>
    </header>
  )
}
