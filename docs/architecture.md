# Forklore — architecture invariants

Orientation for contributors and coding agents. This document records the
decisions that are **load-bearing**: things that look like implementation
details but will silently break the semantics if changed. Read this before
modifying `core/`, writing a strategy, or touching the commit path.

For usage and setup see the README. Everything here is enforced by tests in
`tests/` — when you change semantics, change the tests deliberately, not
accidentally.

## The four rules

1. **The log is the truth.** State is a pure fold over an append-only event
   log: `state(at=N) == fold(log[0..N])`. Nothing writes state directly;
   projections (including "the current state") are replays. Time travel is a
   read (`GET /state?at=N`, `GET /branch/{leaf}?at=N`).
2. **Nothing is ever deleted.** Undo appends *compensating events* (computed
   by `core/undo.py` against the pre-transaction state). Tombstone is the
   only deletion-shaped event, and it only hides.
3. **One request = one transaction = one undo unit.** Multi-node operations
   (strategy runs, diverge, prune) commit all their events in a single tx
   via `store.commit(...)`. Never loop convenience methods to fake it.
4. **The server owns semantics.** Clients render projections and post
   intents; they never reimplement fold/materialize. If the client needs a
   derived view, add a server endpoint (see `/workspace/{node}`).

## Core model (`core/`)

- **Node** = stable identity (its slot in the tree) + versioned content
  (`versions[]`). Children point to identity, never content. `tx` links a
  node to its creation cohort (one generation run).
- **Two edit primitives**, resolving the "typo vs. new storyline" ambiguity:
  - `Revise` — new version on the *same* node; propagates to every branch
    (the typo fix). Bumps `last_revise_seq`.
  - `Variant` + `Graft` (`diverge`) — new sibling + re-parent the subtree;
    branch-local (the alternate storyline).
- **Pins** are edge-anchored `(node, via_child) → version`: they freeze the
  version seen *along one branch only*. A pin whose edge no longer exists is
  inert, not an error.
- **Workspace scope**: `scope="workspace"` nodes are persisted scratch
  (rejected candidates, drafts, critiques, beat roadmaps). They are visible
  in the tree structure but excluded from story materialization and tree
  view. Nothing a strategy produces is discarded.
- **Stale detection**: node N is stale when an ancestor on its path has
  `last_revise_seq > N.created_seq` and the edge is unpinned.
- **GenerationMeta** on every generated node: `{strategy, model, params,
  watermark, context_hash}`. `watermark` = log head the strategy saw;
  `context_hash` = sha256 of the exact prompt messages. This is the
  reproducibility contract — keep it honest.

## Strategy authoring contract (`strategies/`, `server/context.py`)

A strategy is a class with:

```python
id = "my_strategy/0.1"            # stable, versioned; recorded on every node
def __init__(self, model): ...
def for_model(self, model): ...   # return a copy bound to another model
async def generate(self, ctx, params) -> AsyncIterator[str]: ...
```

- **Read** through `ctx`: `materialize()`, `tree_at()` (whole tree at the
  watermark — looking sideways is allowed), `workspace()`. Always
  `ctx.record_prompt(base)` with the *exact* prompt messages so
  `context_hash` covers whatever your truncation/assembly actually kept.
- **Declare results** with `ctx.plan_node(content=..., scope=...,
  meta_extra=..., meta=..., image=...)`. The server commits ALL planned
  nodes in one transaction, stamped with GenerationMeta (`meta_extra` lands
  in `params`); `meta` becomes node meta and `image` is saved to the media
  store and referenced as meta `"image"`, both via SetMeta events in the
  same tx. A strategy that plans nothing falls back to the legacy
  single-append contract.
- **Stream** the user-facing text by yielding tokens. Usually the collected
  winner re-streamed word-by-word, so the UI feels live.
- **Model access** is always `self.model.stream(messages, params)` — an
  async token stream over `Message` lists. Never import LLM SDKs in core;
  per-strategy heavy deps (langgraph) import lazily inside `generate()` and
  stay out of `requirements.txt`'s hard core (they're listed, but the app
  must degrade gracefully without them — see the `find_spec` guard in
  `server/__main__.py` and `pytest.importorskip` in tests).
- **Model rebinding**: when server settings resolve a real model for a
  request, the route calls `strategy.for_model(resolved)`. Registry
  instances are shared — never mutate `self.model` mid-request.
- **Meta-role voices**: orchestrators address the model as `director`,
  `critic`, `drafter`, `planner` (mapped to the `user` chat role by
  `LiteLLMModel.role_of`; add new roles there AND give `DemoModel` a reply
  cycle for them, so the demo and tests keep working offline).

Current strategies: `simple_passthrough/0.1` (baseline),
`best_of_n/0.1` (parallel sampling + critic scoring),
`langgraph_crew/0.1` (sequential pipeline with a revision loop),
`beat_planner/0.1` (hierarchical plan-then-execute; the roadmap is a
workspace node, progress is derived from the tree, not strategy memory),
`crossroads/0.1` (choice-driven play: one passage + three choices per
turn, a `guide` meta-role instruction turn carrying the reader's choice
and one-shot hidden guidance, and a per-node generated image committed
as node meta in the same transaction).

## Server conventions (`server/`)

- Errors: `guard()` maps `KeyError → 404`, `InvariantError → 409`. Raise
  those, not HTTPException, from anything touching the store.
- Generation is SSE: `token` events, then one `committed` event (all
  committed events + the first story event for back-compat), or one `error`
  event. Strategy/model failures mid-stream become `error` events, never
  stack traces.
- `/events/stream` replicates the log (replay from `after`, then follow;
  `follow=false` for tests).
- **Settings** (`server/settings.py`): default model, per-provider API keys,
  per-strategy overrides, default params. Keys are server-side only; GET
  returns a last-4 redacted view. Resolution order: explicit
  `params["model"]` → per-strategy → global default → the strategy's
  built-in (demo) model. The app must work fully offline with zero config.
- **Branches** are names in node meta (`SetMeta key="branch"`), not a schema
  feature — naming/renaming is undoable for free. Prune = tombstone +
  unname in ONE tx.

## Client conventions (`web/`)

- Zustand store = thin projection (`/state`, `/branch/{leaf}`) + session
  state (cursor, undo stack, streaming buffer, panels). Every mutation goes
  through the API, then `refresh()`.
- SSE generation via `ssePost`; log replication via `EventSource` on
  `/events/stream` → debounced refresh.
- Generation provenance renders as chips on message headers (score, rounds,
  beat) with tooltips; per-run artifacts open in the workshop panel
  (`/workspace/{node}`).

## Testing conventions

- `ScriptedModel` for multi-call strategies: one script entry per model
  call, strict order — exhaustion means the strategy made an unexpected
  call (e.g. an unwanted re-plan). This is a feature; write scripts
  exactly as long as the expected call count.
- `tests/test_core.py` encodes the worked trace (seq 1–10) — the
  executable specification of the event semantics.
- Heavy-dependency strategies: `pytest.importorskip` at module/fixture
  level, so the suite stays green in minimal environments.

## Do-not-regress notes (bugs we already fixed once)

- `store.commit`: capture `base = head` BEFORE applying events; per-event
  seqs are `base + i`. Computing head inside the loop produces seq gaps.
- `materialize`: walking the reversed path, `via_child` is
  `path[len(path) - 2 - i]` — off-by-one here throws IndexError on pins.
- LangGraph: node functions must be genuine coroutine functions. A
  `lambda s: self._node(s, base)` is sync-shaped and hides the coroutine —
  use `async def` closures or `functools.partial`.
- Python clears the exception variable after an `except` block — capture
  `str(e)` in a local before defining a closure that reports it.
- SQLite under FastAPI: `check_same_thread=False` + a lock around commit.
- SSE follow loops must have a `follow` escape for tests, or pytest hangs.
- `core/undo.py` SetMeta compensation: use `pre.nodes.get(p.node)` — the
  node may have been created in the same tx (planned commit + SetMeta),
  and direct indexing crashes undo with KeyError.

## Dev workflow

```bash
pip install -r requirements.txt && pip install pytest
python -m pytest                          # 96 tests
python -m server [log.db]                 # API on :8000, demo story seeded
cd web && npm install && npm run dev      # client on :3000
```

`scripts/sync_preview.sh` + `scripts/export_update.sh` are legacy from
sandboxed development (preview packaging, offline git bundles) — harmless
to keep, unneeded for normal local/GitHub workflows.
