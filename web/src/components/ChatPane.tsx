import { useEffect, useRef, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  GitBranch,
  Pencil,
  RefreshCw,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  staleOnBranch,
  useForklore,
  visibleChildren,
} from '@/lib/store'
import type { BranchMsg } from '@/types'
import { Button } from '@/components/ui/button'

function authorLabel(author: string) {
  return author === 'you' ? 'you' : author
}

// ---------------------------------------------------------------------------

function MessageItem({
  msg,
  index,
  isStale,
}: {
  msg: BranchMsg
  index: number
  isStale: boolean
}) {
  const proj = useForklore((s) => s.proj)
  const hovered = useForklore((s) => s.hovered)
  const setHovered = useForklore((s) => s.setHovered)
  const swipe = useForklore((s) => s.swipe)
  const reviseAll = useForklore((s) => s.reviseAll)
  const divergeHere = useForklore((s) => s.divergeHere)
  const regenerate = useForklore((s) => s.regenerate)
  const removeNode = useForklore((s) => s.removeNode)
  const setCursor = useForklore((s) => s.setCursor)
  const branchLen = useForklore((s) => s.branch.length)
  const streaming = useForklore((s) => s.streaming.active)

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(msg.content)

  const node = proj?.nodes[msg.node]
  if (!node) return null

  const isLeaf = index === branchLen - 1
  const sibs = node.parent ? visibleChildren(proj!, node.parent) : []
  const sibIdx = sibs.indexOf(msg.node)

  const startEdit = () => {
    setDraft(msg.content)
    setEditing(true)
  }

  return (
    <article
      className={cn(
        'msg group',
        hovered === msg.node && 'msg-hovered',
        node.tombstoned && 'opacity-50',
      )}
      onMouseEnter={() => setHovered(msg.node)}
      onMouseLeave={() => setHovered(null)}
    >
      <header className="flex items-baseline gap-2.5 mb-2">
        <span className="eyebrow">{authorLabel(msg.author)}</span>
        <span className="seqpill">#{node.created_seq}</span>
        {node.version > 1 && (
          <span className="chip" title={`version ${msg.version} of ${node.version}`}>
            v{msg.version}
            {msg.pinned ? ' · pinned' : ''}
          </span>
        )}
        {isStale && (
          <span
            className="chip chip-stale"
            title="an ancestor was revised after this was written"
          >
            stale
          </span>
        )}
        {node.generation?.params?.score !== undefined && (
          <span
            className="chip"
            title={`critic score ${node.generation.params.score}/10 · rank ${
              Number(node.generation.params.rank) + 1} of ${node.generation.params.n}`}
          >
            score {String(node.generation.params.score)}
          </span>
        )}
        {node.generation && (
          <span className="hint" title={`model: ${node.generation.model} · ctx ${node.generation.context_hash.slice(0, 8)} @ seq ${node.generation.watermark}`}>
            {node.generation.strategy}
          </span>
        )}
      </header>

      {editing ? (
        <div className="space-y-3">
          <textarea
            className="editor"
            rows={Math.max(3, Math.ceil(draft.length / 80))}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!draft.trim()}
              onClick={() => {
                void reviseAll(msg.node, draft)
                setEditing(false)
              }}
              title="same node identity, new content version — propagates to every branch"
            >
              revise · all storylines
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="border-primary/50 text-primary hover:bg-primary/10"
              disabled={!draft.trim()}
              onClick={() => {
                void divergeHere(index, draft)
                setEditing(false)
              }}
              title="new sibling node — only this storyline follows the edit"
            >
              <GitBranch className="size-3.5" />
              diverge · {isLeaf ? 'new variant' : 'this storyline'}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              cancel
            </Button>
          </div>
        </div>
      ) : (
        <p className="manuscript">{msg.content}</p>
      )}

      {!editing && (
        <div className="msg-actions">
          {sibs.length > 1 && (
            <span className="flex items-center gap-1 mr-2">
              <button
                className="icon-btn"
                disabled={sibIdx <= 0}
                onClick={() => void swipe(index, -1)}
                title="previous alternative"
              >
                <ChevronLeft className="size-4" />
              </button>
              <span className="seqpill">{sibIdx + 1} / {sibs.length}</span>
              <button
                className="icon-btn"
                disabled={sibIdx >= sibs.length - 1}
                onClick={() => void swipe(index, 1)}
                title="next alternative"
              >
                <ChevronRight className="size-4" />
              </button>
            </span>
          )}
          <button className="icon-btn" onClick={startEdit} title="edit">
            <Pencil className="size-3.5" />
          </button>
          {node.parent && (
            <button
              className="icon-btn"
              disabled={streaming}
              onClick={() => void regenerate(msg.node)}
              title="regenerate — a new sibling reply"
            >
              <RefreshCw className="size-3.5" />
            </button>
          )}
          {!isLeaf && (
            <button
              className="icon-btn"
              onClick={() => void setCursor(msg.node)}
              title="move the cursor here — continue forks the story from this point"
            >
              <GitBranch className="size-3.5" />
            </button>
          )}
          <button
            className="icon-btn hover:text-primary"
            onClick={() => void removeNode(msg.node)}
            title="tombstone this subtree (recoverable)"
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      )}
    </article>
  )
}

// ---------------------------------------------------------------------------

function Composer() {
  const cursor = useForklore((s) => s.cursor)
  const streaming = useForklore((s) => s.streaming.active)
  const writeMessage = useForklore((s) => s.writeMessage)
  const generate = useForklore((s) => s.generate)
  const [text, setText] = useState('')

  const send = () => {
    if (!text.trim()) return
    void writeMessage(text.trim())
    setText('')
  }

  return (
    <div className="shrink-0 border-t border-border px-8 py-4">
      <div className="mx-auto max-w-[70ch] space-y-3">
        <textarea
          className="editor"
          rows={3}
          placeholder="Continue the story as yourself…"
          value={text}
          disabled={!cursor}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send()
          }}
        />
        <div className="flex items-center justify-between">
          <span className="hint">⌘⏎ writes · generate streams a reply</span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!text.trim() || streaming || !cursor}
              onClick={send}
            >
              write
            </Button>
            <Button
              size="sm"
              className="gap-1.5"
              disabled={streaming || !cursor}
              onClick={() => void generate()}
              title="generate a continuation from the cursor"
            >
              <Sparkles className="size-3.5" />
              {streaming ? 'writing…' : 'generate'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

function EmptyState() {
  const writeMessage = useForklore((s) => s.writeMessage)
  const [text, setText] = useState('')
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="w-full max-w-[56ch] space-y-5">
        <p className="eyebrow">a blank page</p>
        <h1 className="font-serif text-3xl leading-snug">
          Every story here keeps all of its alternatives.
        </h1>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Write an opening line. From then on you can fork anywhere, revise
          history non-destructively, and let strategies draft continuations.
        </p>
        <textarea
          className="editor"
          rows={4}
          placeholder="The door was open…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          autoFocus
        />
        <Button
          disabled={!text.trim()}
          onClick={() => void writeMessage(text.trim())}
        >
          begin the story
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

export default function ChatPane() {
  const proj = useForklore((s) => s.proj)
  const branch = useForklore((s) => s.branch)
  const streaming = useForklore((s) => s.streaming)
  const strategy = useForklore((s) => s.strategy)
  const scrollRef = useRef<HTMLDivElement>(null)

  const stale = proj ? staleOnBranch(proj, branch) : new Set<string>()

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [branch.length, streaming.text])

  if (!proj?.root) {
    return (
      <main className="min-h-0 flex flex-col">
        <EmptyState />
      </main>
    )
  }

  return (
    <main className="min-h-0 flex flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto fl-scroll">
        <div className="mx-auto max-w-[70ch] px-8 py-8">
          {branch.map((m, i) => (
            <MessageItem
              key={m.node}
              msg={m}
              index={i}
              isStale={stale.has(m.node)}
            />
          ))}
          {streaming.active && (
            <article className="msg">
              <header className="flex items-baseline gap-2.5 mb-2">
                <span className="eyebrow">character</span>
                <span className="hint">{strategy}</span>
              </header>
              <p className="manuscript">
                {streaming.text}
                <span className="caret" />
              </p>
            </article>
          )}
        </div>
      </div>
      <Composer />
    </main>
  )
}
