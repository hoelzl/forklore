# Steering and world state — design

Status: approved design, awaiting implementation plan (sequenced after
crossroads; see "Sequencing" below)
Date: 2026-10-17

Two generic, strategy-agnostic mechanisms for steering generation beyond
the story text itself:

- **Steering** — global-ish information that stays mostly consistent
  along and across branches but can be revised like story content:
  intended plot line, tone, canon. Revisions may be global (all
  branches) or branch-local.
- **World state** — position-anchored information that evolves as the
  story advances: what characters are wearing, their current plans or
  state of mind.

Both are accommodated with **no changes to `core/`** by placing
workspace-scope nodes *on the story path*, so the existing edit
primitives supply the semantics. (Rejected alternative: off-path
placement — steering hung off the root, world state as children of
story nodes — which keeps the chain purely passages but requires
invented resolution conventions, gives branch-local steering new
semantics instead of reusing `diverge`, and never flags downstream
staleness on edits.)

## Conventions and tree semantics

Both mechanisms are ordinary nodes with `scope="workspace"` and a meta
marker `SetMeta key="kind"` ∈ `{"steering", "world_state"}`. Workspace
scope means `materialize()` filters them from story text by default and
the tree view already excludes them; being on the path means the fold's
existing machinery does the semantic work.

### Steering

A steering node holds a versioned brief (plot, tone, canon).

- **Scope of application is its subtree.** A global brief lives near
  the root; a subplot brief deeper down. Resolution for a leaf = collect
  the steering nodes on its path, root-most first (general → specific).
- **Revise** propagates the edit to every branch below (the global
  fix). **`diverge`** (Variant + Graft) creates a branch-local brief and
  re-parents the story subtree under it — exactly the meaning of "this
  branch follows a different plot." **Pins** freeze a brief version
  along one branch.
- **Stale detection comes free and is semantically right:** `stale()`
  walks the path regardless of scope, so revising an upstream brief
  flags downstream passages as written-against-an-older-brief.

### World state

A world-state node is a position-anchored snapshot, planned by a
strategy in the **same transaction** as the story node it describes and
chained *after* it:

    … → passage S → state W → (next turn parents under W)

State-after-passage ordering: W describes the world once S has
happened; the branch's current leaf is W, and materialization stays
clean. Undo of the turn is one step because S and W share a `tx`.
Branches inherit the state at the fork point naturally; time travel
(`/state?at=N`) shows the world state as of any seq. A user correction
("she took the coat off two nodes ago") is a Revise on the state node,
propagating along every branch through it and marking downstream nodes
stale.

## Server extensions

1. **Steering insert endpoint** — `POST /nodes/{parent}/steering` with
   `{content}`: commits, in ONE `store.commit`, the steering `Append`
   (`scope="workspace"`) + `SetMeta kind="steering"` + a `Graft` of
   each of the parent's story children under the new node (so the brief
   applies to the whole subtree going forward). Branch-local steering
   afterward is the existing `diverge`. Raises `KeyError`
   (unknown node) / `InvariantError` (e.g. parent tombstoned) →
   `guard()` maps 404/409.
2. **Chained `plan_node`** — `StoreBranchContext.plan_node(...)` gains
   an optional `parent` selector: default `None` = the request leaf
   (current behavior); `"previous"` = the previously planned node in
   this run, which enables the `S → W` chain in one tx. Combines with
   the `meta: dict` parameter from the crossroads spec, so a planned
   world-state node gets its `kind` marker atomically.
3. **Context accessors** (`server/context.py`):
   - `ctx.steering()` → the path's steering nodes, root → leaf, with
     pin-resolved versions (returns `[]` when none).
   - `ctx.world_state()` → the nearest upstream world-state node, or
     `None`.
   Pure read helpers over `tree_at()`; strategies remain free to ignore
   them or assemble their own view via `tree_at()`.
4. **Projection** — `GET /branch/{leaf}` gains `steering` (list) and
   `world_state` (node or null) fields, resolved server-side per rule 4
   ("the server owns semantics"). Backward compatible: existing clients
   ignore unknown fields.

## Strategy contract amendment

Documentation-level addition to the strategy authoring contract in
`docs/architecture.md`: strategies **may** read `ctx.steering()` /
`ctx.world_state()`, fold the results into their prompt however they
see fit (including not at all), and **may** plan on-path workspace
nodes chained after story nodes via `parent="previous"`. Whatever a
strategy includes is covered by `ctx.record_prompt(...)` →
`context_hash`, as always. Neither steering nor world state ever enters
a prompt unless the strategy puts it there.

## Client (`web/`)

Minimal for v1:

- The workbench renders `kind`-marked nodes with distinct chips
  (steering / world state), like the existing provenance chips.
- Steering editing uses the existing revise/diverge flows plus the new
  insert endpoint.
- The crossroads Play view shows the active brief in a collapsible
  panel (from `/branch/{leaf}`'s new fields) — hidden from the story
  text, visible to the reader.

## Error handling

- Store-touching failures raise `KeyError` / `InvariantError` →
  `guard()` → 404/409, unchanged.
- Inserting steering under a tombstoned or unknown parent → 409/404.
- A strategy that chains `parent="previous"` with no previously planned
  node → `InvariantError` at commit time (fail fast, no partial tx).

## Testing

New `tests/test_steering.py`:

- insert endpoint: Append + SetMeta + Grafts share one `tx`; a single
  undo reverts the whole insert;
- steering semantics: Revise propagates to all branches; `diverge`
  stays branch-local; a pin freezes a brief version per branch;
  editing an upstream brief marks downstream nodes stale;
- `ctx.steering()` ordering (root → leaf) and pin resolution;
  `ctx.world_state()` nearest-node selection;
- chained `plan_node`: `S → W` parentage in one tx; `"previous"`
  without a prior planned node fails with `InvariantError`;
- `/branch/{leaf}` projection carries `steering` / `world_state`.
- `ScriptedModel` discipline unchanged (scripts exactly as long as the
  expected call count); `tests/test_core.py` untouched.

## Sequencing

Designed now, **built after crossroads**. The crossroads spec is
amended so its prompt assembly calls `ctx.steering()` from day one
(empty list = no-op), avoiding a prompt-assembly retrofit. World-state
planning lands last and requires no crossroads contract change.

## Explicitly out of scope

- Automatic/LLM-driven maintenance of steering briefs (strategies may
  propose brief updates later; v1 edits are user-initiated).
- Structured/typed world-state schemas (content is free text in v1).
- Surfacing world-state history in the Play view (workbench chips only).
