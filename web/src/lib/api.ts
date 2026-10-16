/** Thin API client: the server owns all semantics; we fetch projections
 *  and post intents. Generation streams over SSE-over-fetch; log
 *  replication uses EventSource on /events/stream. */
import type { SSEvent } from '@/types'

async function req(path: string, init?: RequestInit) {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch { /* keep statusText */ }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  get: (path: string) => req(path),
  post: (path: string, body?: unknown) =>
    req(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? '{}' : JSON.stringify(body),
    }),
}

/** Parse an SSE stream from a POST response body. */
export async function* ssePost(
  path: string,
  body: unknown,
): AsyncGenerator<SSEvent> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) throw new Error(await res.text())
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const evLine = block.split('\n').find((l) => l.startsWith('event: '))
      const dataLine = block.split('\n').find((l) => l.startsWith('data: '))
      if (evLine && dataLine) {
        yield { event: evLine.slice(7), data: JSON.parse(dataLine.slice(6)) }
      }
    }
  }
}
