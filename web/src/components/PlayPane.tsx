import { useEffect, useRef, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { useForklore } from '@/lib/store'
import type { TNode } from '@/types'
import { Button } from '@/components/ui/button'

/** The next turn's choices, from the leaf node's generation params. */
function choicesOf(node: TNode | undefined): string[] {
  const c = node?.generation?.params?.choices
  if (!Array.isArray(c)) return []
  return c.filter((x): x is string => typeof x === 'string')
}

/** The image ref committed as node meta, if any. */
function imageOf(node: TNode | undefined): string | null {
  const img = node?.meta?.image
  return typeof img === 'string' ? img : null
}

function Passage({
  nodeId,
  content,
  onImageLoad,
}: {
  nodeId: string
  content: string
  onImageLoad?: () => void
}) {
  const proj = useForklore((s) => s.proj)
  const img = imageOf(proj?.nodes[nodeId])
  return (
    <article className="msg">
      {img && (
        <img
          className="mb-3 w-full max-w-[40rem] border border-border"
          src={`/media/${img}`}
          alt="illustration for this passage"
          onLoad={onImageLoad}
        />
      )}
      <p className="manuscript">{content}</p>
    </article>
  )
}

function ChoicePanel() {
  const proj = useForklore((s) => s.proj)
  const branch = useForklore((s) => s.branch)
  const streaming = useForklore((s) => s.streaming.active)
  const strategy = useForklore((s) => s.strategy)
  const generate = useForklore((s) => s.generate)
  const [freeform, setFreeform] = useState('')
  const [guidance, setGuidance] = useState('')

  const leafMsg = branch[branch.length - 1]
  const leaf = leafMsg ? proj?.nodes[leafMsg.node] : undefined
  const choices = choicesOf(leaf)

  if (strategy !== 'crossroads/0.1') {
    return (
      <div className="shrink-0 border-t border-border px-8 py-4">
        <div className="mx-auto max-w-[70ch]">
          <span className="hint">
            play mode works with the crossroads/0.1 strategy — pick it in the top bar
          </span>
        </div>
      </div>
    )
  }

  const choose = async (choice?: string) => {
    const params: Record<string, unknown> = {}
    if (choice?.trim()) params.choice = choice.trim()
    if (guidance.trim()) params.guidance = guidance.trim()
    await generate(undefined, params)
    if (!useForklore.getState().error) {
      setFreeform('')
      setGuidance('')
    }
  }

  return (
    <div className="shrink-0 border-t border-border px-8 py-4">
      <div className="mx-auto max-w-[70ch] space-y-3">
        {choices.length > 0 && (
          <div className="flex flex-col gap-2">
            {choices.map((c, i) => (
              <Button
                key={`${i}-${c}`}
                variant="outline"
                size="sm"
                className="justify-start font-serif italic"
                disabled={streaming}
                onClick={() => void choose(c)}
              >
                {c}
              </Button>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <input
            className="editor flex-1"
            placeholder="…or choose your own path"
            aria-label="choose your own path"
            value={freeform}
            disabled={streaming}
            onChange={(e) => setFreeform(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && freeform.trim()) void choose(freeform)
            }}
          />
          <Button
            size="sm"
            className="gap-1.5"
            disabled={streaming || !freeform.trim()}
            onClick={() => void choose(freeform)}
            title="generate the continuation for your choice"
          >
            <Sparkles className="size-3.5" />
            {streaming ? 'writing…' : 'choose'}
          </Button>
        </div>
        <details>
          <summary className="hint cursor-pointer select-none">
            guidance for the model (not part of the story)
          </summary>
          <textarea
            className="editor mt-2"
            rows={2}
            placeholder="e.g. slow the pacing down; keep Mara suspicious of the keeper"
            value={guidance}
            disabled={streaming}
            onChange={(e) => setGuidance(e.target.value)}
          />
        </details>
      </div>
    </div>
  )
}

function EmptyState() {
  const writeMessage = useForklore((s) => s.writeMessage)
  const [text, setText] = useState('')
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="w-full max-w-[56ch] space-y-5">
        <p className="eyebrow">a blank page</p>
        <h1 className="font-serif text-3xl leading-snug">
          Every passage ends at a crossroads.
        </h1>
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

export default function PlayPane() {
  const proj = useForklore((s) => s.proj)
  const branch = useForklore((s) => s.branch)
  const streaming = useForklore((s) => s.streaming)
  const scrollRef = useRef<HTMLDivElement>(null)

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
    <main className="min-h-0 flex">
      <div className="min-w-0 flex-1 flex flex-col">
        <div ref={scrollRef} className="flex-1 overflow-y-auto fl-scroll">
          <div className="mx-auto max-w-[70ch] px-8 py-8">
            {branch.map((m) => (
              <Passage
                key={m.node}
                nodeId={m.node}
                content={m.content}
                onImageLoad={() => {
                  const el = scrollRef.current
                  if (el) el.scrollTop = el.scrollHeight
                }}
              />
            ))}
            {streaming.active && (
              <article className="msg">
                <p className="manuscript">
                  {streaming.text}
                  <span className="caret" />
                </p>
              </article>
            )}
          </div>
        </div>
        <ChoicePanel />
      </div>
    </main>
  )
}
