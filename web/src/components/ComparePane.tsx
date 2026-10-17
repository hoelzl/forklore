/** Side-by-side comparison of two storylines: the shared prefix is
 *  dimmed, a marker shows where the paths diverge. Read-only. */
import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '@/lib/api'
import { useForklore } from '@/lib/store'
import type { BranchMsg } from '@/types'

function Column({
  title, msgs, divergeAt,
}: {
  title: string
  msgs: BranchMsg[]
  divergeAt: number
}) {
  return (
    <div className="min-w-0 flex-1 flex flex-col min-h-0">
      <header className="shrink-0 px-6 py-3 border-b border-border">
        <span className="font-serif italic">{title}</span>
      </header>
      <div className="flex-1 overflow-y-auto fl-scroll px-6 py-5 space-y-5">
        {msgs.slice(0, divergeAt).map((m) => (
          <p key={m.node} className="manuscript text-[15px] opacity-45">
            {m.content}
          </p>
        ))}
        <p className="eyebrow text-primary">— storylines diverge —</p>
        {msgs.slice(divergeAt).map((m) => (
          <div key={m.node}>
            <p className="eyebrow mb-1">{m.author}</p>
            <p className="manuscript text-[15px]">{m.content}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ComparePane() {
  const compare = useForklore((s) => s.compare)
  const branches = useForklore((s) => s.branches)
  const setCompare = useForklore((s) => s.setCompare)
  const [msgs, setMsgs] = useState<{ a: BranchMsg[]; b: BranchMsg[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!compare) return
    let cancelled = false
    setMsgs(null)
    Promise.all([
      api.get(`/branch/${compare.a}`),
      api.get(`/branch/${compare.b}`),
    ]).then(([ra, rb]) => {
      if (!cancelled) setMsgs({ a: ra.messages, b: rb.messages })
    }).catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [compare])

  if (!compare) return null

  const nameOf = (node: string) =>
    branches.find((b) => b.node === node)?.name ?? node.slice(0, 8)

  let divergeAt = 0
  if (msgs) {
    const n = Math.min(msgs.a.length, msgs.b.length)
    while (divergeAt < n && msgs.a[divergeAt].node === msgs.b[divergeAt].node) {
      divergeAt++
    }
  }

  return (
    <main className="min-h-0 flex flex-col">
      <div className="shrink-0 flex items-center gap-3 px-6 py-2.5 border-b border-border">
        <span className="eyebrow">comparing storylines</span>
        <div className="flex-1" />
        <button className="icon-btn" onClick={() => setCompare(null)} title="exit comparison">
          <X className="size-4" />
        </button>
      </div>
      <div className="flex-1 min-h-0 flex divide-x divide-border">
        {error && <p className="p-6 text-sm text-destructive">{error}</p>}
        {!msgs && !error && <p className="p-6 hint">loading…</p>}
        {msgs && (
          <>
            <Column title={nameOf(compare.a)} msgs={msgs.a} divergeAt={divergeAt} />
            <Column title={nameOf(compare.b)} msgs={msgs.b} divergeAt={divergeAt} />
          </>
        )}
      </div>
    </main>
  )
}
