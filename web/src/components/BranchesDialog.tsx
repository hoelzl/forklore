/** Branch management: name the storyline at the cursor, jump between
 *  named storylines, prune (recoverable — one undo unit), and pick two
 *  branches for side-by-side comparison. */
import { useEffect, useState } from 'react'
import { GitBranch, GitCompare, Scissors, X } from 'lucide-react'
import { useForklore } from '@/lib/store'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export default function BranchesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const branches = useForklore((s) => s.branches)
  const cursor = useForklore((s) => s.cursor)
  const proj = useForklore((s) => s.proj)
  const setCursor = useForklore((s) => s.setCursor)
  const nameBranch = useForklore((s) => s.nameBranch)
  const unnameBranch = useForklore((s) => s.unnameBranch)
  const pruneBranch = useForklore((s) => s.pruneBranch)
  const setCompare = useForklore((s) => s.setCompare)

  const [newName, setNewName] = useState('')
  const [cmpA, setCmpA] = useState('')
  const [cmpB, setCmpB] = useState('')

  useEffect(() => {
    if (open) {
      setNewName('')
      setCmpA(branches[0]?.name ?? '')
      setCmpB(branches[1]?.name ?? '')
    }
  }, [open, branches])

  const cursorName = cursor && proj?.nodes[cursor]?.meta?.branch
    ? String(proj.nodes[cursor].meta.branch) : null

  const name = async () => {
    if (!newName.trim() || !cursor) return
    if (await nameBranch(newName.trim(), cursor)) setNewName('')
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-serif italic text-xl">Storylines</DialogTitle>
          <DialogDescription>
            Names hang on nodes, like lightweight bookmarks. Pruning hides a
            storyline — recoverably, in a single undo step.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          {branches.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No named storylines yet.
            </p>
          )}
          {branches.map((b) => (
            <div key={b.name} className="flex items-center gap-2 group">
              <button
                className="flex-1 min-w-0 flex items-baseline gap-2 text-left rounded px-2 py-1.5 hover:bg-accent transition-colors"
                onClick={() => { void setCursor(b.node); onClose() }}
                title={`jump to ${b.name} — node #${b.created_seq}, depth ${b.depth}`}
              >
                <span className="font-serif italic truncate">{b.name}</span>
                <span className="seqpill shrink-0">#{b.created_seq}</span>
                {b.hidden && <span className="chip chip-stale shrink-0">hidden</span>}
              </button>
              <button
                className="icon-btn opacity-0 group-hover:opacity-100"
                title="remove the name (storyline untouched)"
                onClick={() => void unnameBranch(b.name)}
              >
                <X className="size-3.5" />
              </button>
              <button
                className="icon-btn opacity-0 group-hover:opacity-100 hover:text-primary"
                title="prune: tombstone this node and its subtree (one undo step restores all)"
                onClick={() => void pruneBranch(b.name)}
              >
                <Scissors className="size-3.5" />
              </button>
            </div>
          ))}
        </div>

        <div className="space-y-2 pt-2 border-t border-border">
          <Label htmlFor="branch-name">
            Name the current point{cursorName ? ` (renames “${cursorName}”)` : ''}
          </Label>
          <div className="flex gap-2">
            <Input
              id="branch-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void name() }}
              placeholder="mirror-timeline…"
              className="font-mono text-xs"
              disabled={!cursor}
            />
            <Button size="sm" variant="outline" disabled={!newName.trim() || !cursor}
              onClick={() => void name()}>
              <GitBranch className="size-3.5 mr-1" /> name
            </Button>
          </div>
        </div>

        {branches.length >= 2 && (
          <div className="space-y-2 pt-2 border-t border-border">
            <Label>Compare two storylines</Label>
            <div className="flex items-center gap-2">
              <select
                className="chip bg-transparent cursor-pointer flex-1"
                value={cmpA}
                onChange={(e) => setCmpA(e.target.value)}
              >
                {branches.map((b) => (
                  <option key={b.name} value={b.name} className="bg-popover">{b.name}</option>
                ))}
              </select>
              <span className="hint">vs</span>
              <select
                className="chip bg-transparent cursor-pointer flex-1"
                value={cmpB}
                onChange={(e) => setCmpB(e.target.value)}
              >
                {branches.map((b) => (
                  <option key={b.name} value={b.name} className="bg-popover">{b.name}</option>
                ))}
              </select>
              <Button
                size="sm"
                disabled={!cmpA || !cmpB || cmpA === cmpB}
                onClick={() => {
                  const a = branches.find((b) => b.name === cmpA)
                  const bb = branches.find((b) => b.name === cmpB)
                  if (a && bb) { setCompare({ a: a.node, b: bb.node }); onClose() }
                }}
              >
                <GitCompare className="size-3.5 mr-1" /> compare
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
