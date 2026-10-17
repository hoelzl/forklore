# Crossroads play mode — design

Status: approved design, awaiting implementation plan
Date: 2026-10-17

A choice-driven play mode for forklore: a story starts at an opening node;
at each generated node the reader is offered **three choices** (plus a
free-form choice input) and a **guidance box** whose contents steer the
model without appearing in the rendered story text. Every generated node
is accompanied by a **generated image** derived from its text.

Approach: **strategy-layer addition** — no changes to `core/`. Choices and
image references ride the existing `GenerationMeta.params` / `SetMeta`
idioms, the same way branches already do. (Rejected alternatives: typed
core schema events — regression risk to fold/undo for no current need; a
separate standalone client — loses the tree/workbench UI that makes taken
choices navigable branch points.)

## Turn cycle

A play session is a path through the existing tree.

1. The story starts at an opening node (the existing root/seed — no new
   concept).
2. The client posts `/generate` with `strategy: "crossroads/0.1"`, `leaf`,
   and optional `params: {choice: "...", guidance: "..."}`.
3. The strategy streams the continuation text (existing SSE `token`
   events), then generates an image, then commits **one transaction**:
   the story `Append` plus a `SetMeta` carrying the image reference.
4. The node's `GenerationMeta.params` carries `choices: [c1, c2, c3]` —
   the labels offered for the *next* turn — plus the `choice` and
   `guidance` that produced this node.
5. The client renders text + image + three choice buttons + free-form
   input + guidance box. Picking a choice is another `/generate` with
   `params.choice` set. Undo, branching, time-travel, and the workbench
   view keep working untouched.

## Strategy: `crossroads/0.1`

New file `strategies/crossroads.py`, following the strategy authoring
contract (`id`, `__init__(model)`, `for_model`, async `generate(ctx,
params)` yielding tokens).

**Prompt assembly:** `ctx.materialize()` for the story so far, then one
trailing `director` meta-role message combining the chosen label and the
one-shot guidance:

    The reader chose: <choice>. Direction: <guidance>.
    Write the next passage, then propose three choices.

`director` is reused, so `LiteLLMModel.role_of` needs no new role.
`ctx.record_prompt(...)` is called with the exact messages, per the
reproducibility contract.

**Steering read-path (amendment, 2026-10-17):** prompt assembly first
calls `ctx.steering()` (see
`2026-10-17-steering-and-world-state-design.md`) and prepends any
resolved steering briefs as `director` messages before the
choice/guidance message. When no steering node exists the accessor
returns an empty list and the strategy behaves exactly as originally
specified. Planning world-state nodes is *not* part of crossroads v1.

**Structured output:** the model replies in a small delimited format
(`PASSAGE:` / `CHOICES:` sections). The strategy parses the reply, plans
the story node with

    meta_extra={"choices": [...], "choice": ..., "guidance": ...,
                "image_prompt": ...}

and re-streams the passage text word-by-word as tokens. Where a provider
supports structured/JSON output natively that may be used instead, but
the delimited format is the baseline because it works with every model
including `DemoModel`.

**Placement of data (judgment calls, settled):**

- Choices live in `GenerationMeta.params` (via `meta_extra`), not node
  `meta`: they are fixed at generation time and belong to provenance.
- Guidance is recorded in `params` too. It is hidden from the *rendered
  story* (it never becomes a node, so `materialize()` never returns it),
  not from the log — that is what keeps `context_hash` honest.
- Guidance is **one-shot per turn**: it applies to the next generation
  only, is recorded in that node's params, and clears after use.
  Persistent steering is a separate, complementary feature covered by
  the steering/world-state spec (see the amendment above).

**Demo model:** `DemoModel` gains a choices-capable reply cycle
(`PASSAGE:` / `CHOICES:` formatted text) so the whole loop works fully
offline with zero configuration.

## Image subsystem

**Protocol** (`strategies/images.py`, mirrors `ChatModel`):

```python
class ImageModel(Protocol):
    name: str
    async def generate(self, prompt: str, params: dict[str, Any]) -> bytes: ...
```

- `DemoImageModel` — deterministic, offline, zero config: renders a small
  SVG "cover plate" derived from a hash of the passage text (gradient/
  pattern + a title word). A placeholder, but one that exercises the
  whole pipeline end to end.
- A provider-backed implementation (e.g. an OpenAI-images client) —
  lazy-imported, bound via the existing settings resolution path. Image
  provider settings/keys live in `server/settings.py` beside the chat
  ones; keys stay server-side and the redacted GET view is unchanged.
  The app must still run fully offline with no image provider
  configured (demo image model).

The strategy derives the image prompt from the passage text via a fixed
template plus the passage's first 40 words, recorded in
`params.image_prompt`.

**Storage:** image bytes go to a server-managed media directory
(`<logfile>.media/<sha256>.<ext>`), never into the SQLite event log. The
node receives `SetMeta key="image", value="media/<sha256>.<ext>"`
committed in the **same transaction** as the story `Append`. Undo/redo/
tombstone semantics come free, and time travel (`/state?at=N`) resolves
the correct image for every historical node. The media directory is
gitignored like `*.db` files.

## Server changes

Two small extensions, both in the existing commit path:

1. `StoreBranchContext.plan_node(...)` gains an optional `meta: dict`
   parameter. The `/generate` commit block emits a `SetMeta` event
   immediately after each planned `Append` for every declared meta key —
   still a single `store.commit(...)`, still one undo unit.
2. `GET /media/{name}` serves bytes from the media directory. The name
   must match `^[a-f0-9]{64}\.(svg|png)$` — anything else is 404, so
   path traversal is impossible by construction.

**SSE flow:** `token` events stream text as today; the `committed`
payload already carries the `SetMeta` event with the image reference, so
no new SSE event type is required. A mid-stream image failure degrades
to "no image this node" — never an `error` event that kills the text.
The story is primary; the picture is decoration.

## Client (`web/`)

A play-mode view alongside the existing workbench, not a replacement.

- A "Play" view (a new route in the existing client, linked from the
  current UI) renders the materialized path of the current branch as
  a scroll of passages, each with its image (`GET /media/...` via node
  meta). On the leaf: the three choice buttons (from
  `generation.params.choices`), a free-form choice input, and a
  collapsible guidance box.
- All mutations go through the API then `refresh()`, per convention —
  a choice click is `ssePost /generate` with `params.choice`; free-form
  text and guidance land in the same params. No fold/materialize logic
  client-side.
- The tree/workbench view stays fully functional: every taken choice is
  a real branch point, so "go back and take the other path" is clicking
  the existing tree. Choice labels render as provenance chips on node
  headers, like score/rounds/beat today.

## Error handling

- Strategy/model failures mid-stream → SSE `error` event (existing
  convention).
- Malformed model output (missing `CHOICES:` block) → the strategy
  degrades: commit the passage with an empty choices list and let the
  free-form box carry the turn.
- Image generation failure → the node commits without `image` meta; the
  client shows a placeholder frame.
- Store-touching errors raise `KeyError`/`InvariantError` → `guard()`
  maps to 404/409, unchanged.

## Testing

Following existing conventions:

- `ScriptedModel` scripts sized exactly to the expected call count
  (exhaustion is a feature); `DemoImageModel` doubles as the image test
  double since it is deterministic.
- New `tests/test_crossroads.py`:
  - prompt assembly — guidance present in `ctx.prompt`, absent from
    `materialize()` output;
  - choices parsing, including the malformed-output degradation path;
  - `meta_extra` → params landing on the committed node;
  - one-tx commit: `Append` + `SetMeta` share a `tx`; undo reverts the
    whole turn in one step;
  - image-failure degradation (node commits, no image meta).
- Server tests: `/media` serving and traversal rejection; the
  planned-commit `SetMeta` path.
- `tests/test_core.py` untouched — that is the proof core semantics did
  not move.

## Explicitly out of scope

- Persistent story steering and world state — covered by the companion
  spec `2026-10-17-steering-and-world-state-design.md`; crossroads ships
  with the steering read-path only (see the amendment above).
- Pre-generating full continuations for each choice (choices are labels).
- Typed core schema events for choices or media.
- Editing/regenerating images for historical nodes (Revise keeps the
  original image meta; regeneration can be a follow-up).
