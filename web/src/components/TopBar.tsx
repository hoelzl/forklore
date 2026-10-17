import { useEffect, useState } from 'react'
import { GitBranch, Settings2, Undo2 } from 'lucide-react'
import { useForklore } from '@/lib/store'
import { Button } from '@/components/ui/button'
import SettingsDialog from '@/components/SettingsDialog'
import BranchesDialog from '@/components/BranchesDialog'

export default function TopBar() {
  const head = useForklore((s) => s.proj?.head ?? 0)
  const strategies = useForklore((s) => s.strategies)
  const strategy = useForklore((s) => s.strategy)
  const setStrategy = useForklore((s) => s.setStrategy)
  const undoCount = useForklore((s) => s.undoStack.length)
  const undo = useForklore((s) => s.undo)
  const streaming = useForklore((s) => s.streaming.active)
  const settings = useForklore((s) => s.settings)
  const loadSettings = useForklore((s) => s.loadSettings)
  const branchCount = useForklore((s) => s.branches.length)
  const play = useForklore((s) => s.play)
  const setPlay = useForklore((s) => s.setPlay)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [branchesOpen, setBranchesOpen] = useState(false)

  useEffect(() => { void loadSettings() }, [loadSettings])

  const activeModel = strategy && settings
    ? settings.strategy_models[strategy] ?? settings.default_model
    : null

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
      <span className="flex gap-1">
        <Button
          variant={play ? 'outline' : 'ghost'}
          size="sm"
          onClick={() => setPlay(true)}
          title="play mode: read, choose, steer"
        >
          play
        </Button>
        <Button
          variant={!play ? 'outline' : 'ghost'}
          size="sm"
          onClick={() => setPlay(false)}
          title="workbench mode: tree, edits, workshop"
        >
          workbench
        </Button>
      </span>
      <button
        className="chip bg-transparent cursor-pointer hover:text-foreground transition-colors"
        title={activeModel
          ? `model: ${activeModel} — click to change`
          : 'no model configured — using the built-in demo model. Click to configure.'}
        onClick={() => setSettingsOpen(true)}
      >
        {activeModel ?? 'demo model'}
      </button>
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
      <Button
        variant="ghost"
        size="sm"
        className="gap-1.5"
        onClick={() => setBranchesOpen(true)}
        title="named storylines: jump, rename, prune, compare"
      >
        <GitBranch className="size-4" />
        {branchCount > 0 && <span className="text-xs">{branchCount}</span>}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setSettingsOpen(true)}
        title="model settings"
      >
        <Settings2 className="size-4" />
      </Button>
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <BranchesDialog open={branchesOpen} onClose={() => setBranchesOpen(false)} />
    </header>
  )
}
