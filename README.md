# Forklore

A workbench for **branching, LLM-assisted storytelling** — like SillyTavern's
chat interface, but built on version-control semantics, with a pluggable
strategy layer for experimenting with generation architectures.

Every reply alternative is kept. Every edit is non-destructive. Every fork is
a first-class storyline you can return to. And every generated node records
exactly which strategy, model, and context produced it.

## The model

The conversation is a **tree of nodes with stable identity and versioned
content**; every change is an event in an **append-only log** (SQLite), and
the visible state is a pure fold over that log:

```
state(at=N) == fold(log[0..N])        # time travel is a read
undo      == compensating events      # the log only ever grows
```

Two edit primitives resolve the "typo vs. divergence" ambiguity:

| intent | primitive | effect |
|---|---|---|
| fix something, everywhere | `Revise` | new content version on the *same* node; propagates to all branches |
| take the story elsewhere | `Variant` + `Graft` (`diverge`) | new sibling node; only this storyline follows it |

Additional machinery: branch-scoped **version pins**, tombstone/restore (the
only kind of deletion), **stale detection** (nodes written against since-
revised ancestors), and a **workspace scope** where strategies persist
scratch work (rejected candidates, critic notes) without polluting the story.

## Generation strategies

Strategies implement one narrow interface — read the tree through a
`BranchContext`, stream tokens, declare result nodes with `plan_node()` —
and the server commits each generation as **one transaction** stamped with
`{strategy, model, params, watermark, context_hash}` for full
reproducibility.

- `simple_passthrough/0.1` — materialized branch → one model call. The baseline.
- `best_of_n/0.1` — N candidates, critic-scored, top-k committed as story
  siblings; all drafts retained in the workspace.

The interface is designed so that a multi-agent orchestrator (director /
drafter / critic, LangGraph-based, …) is just another strategy.

## Layout

```
core/          event schema, fold, materialize, undo — pure Python + pydantic,
               zero LLM deps (the scientific instrument; test it, trust it)
server/        FastAPI + SSE: commit routes, projections, strategy registry,
               generation streaming, resumable log replication
strategies/    strategy implementations + model clients (Fake/Scripted/Demo/
               LiteLLM)
web/           React + TypeScript + Vite client: manuscript chat pane with
               swipe/edit/diverge, React Flow story tree, SSE streaming
tests/         48 tests encoding the semantics (the worked trace included)
Dockerfile     all-in-one image (web build + server)
```

## Run it

**Docker (everything, with a deterministic demo model):**

```bash
docker build -t forklore . && docker run -p 8000:8000 forklore
# open http://localhost:8000
```

**Dev (two terminals):**

```bash
pip install -r requirements.txt
python -m server                 # API on :8000, seeds a demo story
cd web && npm install && npm run dev   # client on :3000, proxies to :8000
```

**Real models:** register `LiteLLMModel` in `server/__main__.py`
(`pip install litellm`, set your provider keys as usual).

**Tests:** `pip install pytest && python -m pytest`

## API sketch

`POST /nodes` · `POST /nodes/{id}/revise|variant|diverge|tombstone|restore|pin|unpin`
· `POST /undo/{tx}` — one request = one transaction.
`GET /state?at=N` · `GET /branch/{leaf}?at=N` · `GET /events?after=N` —
projections, watermark reads, log replication.
`POST /generate` — SSE token stream, then the committed event(s).
`GET /events/stream` — live log tail (SSE, resumable by seq).
