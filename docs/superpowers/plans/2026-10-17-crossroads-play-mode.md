# Crossroads Play Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a choice-driven play mode to forklore: the `crossroads/0.1` strategy generates a passage plus three choices per turn (with free-form choice and hidden one-shot guidance), each generated node gets a generated image, and the web client gains a Play view.

**Architecture:** Pure strategy-layer addition per the approved spec
(`docs/superpowers/specs/2026-10-17-crossroads-play-mode-design.md`). No
`core/` changes. Choices/provenance ride in `GenerationMeta.params`; image
bytes live in a content-addressed media store referenced by node meta
(`SetMeta key="image"`) committed in the same transaction as the story
`Append`. The steering read-path (`ctx.steering()`) ships in minimal form;
insert endpoints/projections belong to the later steering spec.

**Spec deviation (deliberate):** the crossroads instruction turn uses a new
meta-role `guide` instead of reusing `director`. `DemoModel` dispatches
replies by the *last* message's author, and `director` is already bound to
`langgraph_crew`'s direction lines — reusing it would break one demo or the
other. `guide` is added to `LiteLLMModel.role_of` AND `DemoModel`, per the
AGENTS.md convention. Steering briefs are still prepended as `director`
messages (they are never the last message).

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, pytest (backend);
React 19, TypeScript, Vite, Tailwind, Zustand (frontend).

**Conventions to respect (from AGENTS.md / docs/architecture.md):**

- One request = one transaction = one undo unit; all planned nodes commit
  via a single `store.commit(...)`.
- `core/` stays pure — no changes at all in this plan.
- Backend errors from store-touching code: raise `KeyError` /
  `InvariantError`, mapped by `guard()`. Don't raise `HTTPException` there
  (the `/media` endpoint is *not* store-touching and may use
  `HTTPException`).
- `ScriptedModel` scripts are exactly as long as the expected model call
  count — exhaustion is a feature.
- The app must work fully offline with zero config (DemoModel +
  DemoImageModel).
- Run all backend commands with the venv activated
  (`source .venv/Scripts/activate` in Git Bash).

---

## File structure

| File | Responsibility |
|---|---|
| `strategies/crossroads.py` | `crossroads/0.1` strategy + `parse_crossroads_output` |
| `strategies/images.py` | `ImageModel` protocol, `DemoImageModel`, `LiteLLMImageModel` |
| `strategies/models.py` | add `guide` meta-role (role_of + DemoModel cycle) |
| `server/media.py` | `MediaStore` — content-addressed image bytes |
| `server/context.py` | `plan_node` gains `meta`/`image`; new `steering()` accessor |
| `server/app.py` | planned-commit emits `SetMeta`; `/media/{name}`; image-model rebinding |
| `server/settings.py` | `image_model` setting + `build_image_model` |
| `server/__main__.py` | register crossroads, wire disk-backed media dir |
| `tests/test_crossroads.py` | parser + strategy tests |
| `tests/test_media.py` | MediaStore + `/media` endpoint tests |
| `tests/test_steering.py` | `ctx.steering()` tests (extended later by the steering spec) |
| `web/src/lib/store.ts` | `generate(leaf?, params?)`, `play` mode flag |
| `web/src/components/PlayPane.tsx` | the Play view |
| `web/src/components/TopBar.tsx` | Play/Workbench toggle |
| `web/src/App.tsx` | render PlayPane in play mode |
| `web/vite.config.ts` | proxy `/media` in dev |
| `docs/architecture.md`, `README.md` | strategy list updates |
| `.gitignore` | ignore `*.media/` dirs |

---

## Task 1: Crossroads output parser

**Files:**
- Create: `strategies/crossroads.py`
- Test: `tests/test_crossroads.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_crossroads.py`:

```python
"""crossroads: PASSAGE:/CHOICES: parsing, choice/guidance provenance,
one-tx commits with image SetMeta, degradation paths."""
import pytest

from strategies.crossroads import parse_crossroads_output


def test_parses_standard_format():
    text = ("PASSAGE:\nShe stepped inside, and the dust rose to meet her.\n\n"
            "CHOICES:\n- Light a match\n- Call into the dark\n- Take the stairs")
    passage, choices = parse_crossroads_output(text)
    assert passage == "She stepped inside, and the dust rose to meet her."
    assert choices == ["Light a match", "Call into the dark", "Take the stairs"]


def test_numbered_choices_accepted():
    text = "PASSAGE:\nA.\n\nCHOICES:\n1. one\n2. two\n3) three"
    assert parse_crossroads_output(text)[1] == ["one", "two", "three"]


def test_missing_choices_block_degrades():
    passage, choices = parse_crossroads_output("PASSAGE:\nJust the text.")
    assert passage == "Just the text."
    assert choices == []


def test_missing_passage_marker_uses_whole_reply():
    passage, choices = parse_crossroads_output("No markers at all.")
    assert passage == "No markers at all."
    assert choices == []


def test_extra_choices_trimmed_to_three():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n- b\n- c\n- d"
    assert parse_crossroads_output(text)[1] == ["a", "b", "c"]


def test_empty_choice_lines_skipped():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n\n- b\n"
    assert parse_crossroads_output(text)[1] == ["a", "b"]


def test_bare_marker_choice_lines_skipped():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n-\n- b\n- c"
    assert parse_crossroads_output(text)[1] == ["a", "b", "c"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_crossroads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.crossroads'`

- [ ] **Step 3: Implement the parser**

Create `strategies/crossroads.py`:

```python
"""crossroads: choice-driven play strategy.

Each turn: the materialized branch (+ optional steering briefs) + one
`guide` meta-role message carrying the reader's choice and the author's
one-shot hidden guidance -> one model call in a PASSAGE:/CHOICES: format
-> ONE story node whose GenerationMeta.params carry the choices offered
next turn and the choice/guidance that produced this node. A generated
image (content-addressed in the media store) is committed as node meta
in the same transaction.
"""
from __future__ import annotations

import re

_CHOICE_LINE_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s*")


def parse_crossroads_output(text: str) -> tuple[str, list[str]]:
    """Split a model reply into (passage, choices).

    Degrades gracefully: no PASSAGE: marker -> the whole reply is the
    passage; no CHOICES: marker -> no choices. Choice lines may be
    bulleted or numbered; bare markers and blank lines are skipped;
    at most three are kept.
    """
    body = text.strip()
    choices: list[str] = []
    if "CHOICES:" in body:
        body, _, choice_block = body.partition("CHOICES:")
        for line in choice_block.strip().splitlines():
            choice = _CHOICE_LINE_RE.sub("", line.strip()).strip()
            if choice:
                choices.append(choice)
        choices = choices[:3]
    if "PASSAGE:" in body:
        body = body.split("PASSAGE:", 1)[1]
    return body.strip(), choices
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_crossroads.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add strategies/crossroads.py tests/test_crossroads.py
git commit -m "crossroads: PASSAGE/CHOICES output parser"
```

---

## Task 2: Image models

**Files:**
- Create: `strategies/images.py`
- Test: `tests/test_media.py` (image-model tests live here with the media tests)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_media.py`:

```python
"""MediaStore (content-addressed image bytes) and DemoImageModel."""
import asyncio

import pytest

from strategies.images import DemoImageModel


def _gen(model, prompt):
    return asyncio.run(_collect(model, prompt))


async def _collect(model, prompt):
    return await model.generate(prompt, {})


def test_demo_image_is_deterministic():
    model = DemoImageModel()
    assert _gen(model, "a lighthouse at night") == _gen(model, "a lighthouse at night")


def test_demo_image_is_svg_with_title_word():
    svg = _gen(DemoImageModel(), "the fog pressed its face").decode()
    assert svg.startswith("<svg")
    assert "The" in svg  # first content word, titled


def test_demo_image_varies_with_prompt():
    model = DemoImageModel()
    assert _gen(model, "one story") != _gen(model, "another story")


def test_demo_image_metadata():
    assert DemoImageModel.ext == "svg"
    assert DemoImageModel.name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_media.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.images'`

- [ ] **Step 3: Implement `strategies/images.py`**

```python
"""Image models: the visual counterpart of ChatModel.

Provider diversity is a strategy-layer concern, exactly like chat models:
`DemoImageModel` keeps the app fully offline and deterministic;
`LiteLLMImageModel` is the production option, lazily imported.
"""
from __future__ import annotations

import hashlib
from typing import Any, Protocol


class ImageModel(Protocol):
    name: str
    ext: str  # media file extension: "svg" | "png"

    async def generate(self, prompt: str, params: dict[str, Any]) -> bytes: ...


class DemoImageModel:
    """Deterministic offline cover plate: an SVG gradient derived from
    the prompt hash, titled with the prompt's first content word."""

    name = "demo-image"
    ext = "svg"

    async def generate(self, prompt: str, params: dict[str, Any]) -> bytes:
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        hue1 = int(digest[:4], 16) % 360
        hue2 = (hue1 + 40 + int(digest[4:6], 16) % 80) % 360
        word = next(
            (w.strip(".,;:\"'()[]").title() for w in prompt.split()
             if w.strip(".,;:\"'()[]").isalpha()),
            "Passage")
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="hsl({hue1},45%,22%)"/>'
            f'<stop offset="1" stop-color="hsl({hue2},60%,45%)"/>'
            '</linearGradient></defs>'
            '<rect width="640" height="360" fill="url(#g)"/>'
            '<text x="32" y="328" font-family="Georgia, serif" font-size="28" '
            f'fill="rgba(255,255,255,0.85)" font-style="italic">{word}</text>'
            '</svg>')
        return svg.encode()


class LiteLLMImageModel:
    """Provider-backed images via litellm (pip install litellm).

    Requires a provider that returns b64_json; URL-only responses raise
    RuntimeError rather than fetching (no hidden network reads)."""

    ext = "png"

    def __init__(self, model: str, **defaults):
        import litellm  # optional dependency, imported lazily
        self._litellm = litellm
        self.name = model
        self.defaults = defaults

    async def generate(self, prompt: str, params: dict[str, Any]) -> bytes:
        import base64
        resp = await self._litellm.aimage_generation(
            model=self.name, prompt=prompt,
            **{**self.defaults, **params})
        b64 = resp.data[0].b64_json
        if not b64:
            raise RuntimeError(
                f"image model {self.name!r} returned no b64_json payload")
        return base64.b64decode(b64)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_media.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add strategies/images.py tests/test_media.py
git commit -m "strategies: ImageModel protocol + DemoImageModel + LiteLLMImageModel"
```

---

## Task 3: MediaStore

**Files:**
- Create: `server/media.py`
- Test: `tests/test_media.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_media.py`:

```python
from server.media import MediaStore

_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_save_and_read_roundtrip_memory():
    store = MediaStore()
    name = store.save(_SVG)
    assert name.endswith(".svg")
    data, content_type = store.read(name)
    assert data == _SVG
    assert content_type == "image/svg+xml"


def test_save_and_read_roundtrip_disk(tmp_path):
    store = MediaStore(str(tmp_path))
    name = store.save(_PNG)
    assert name.endswith(".png")
    assert (tmp_path / name).read_bytes() == _PNG
    data, content_type = MediaStore(str(tmp_path)).read(name)
    assert data == _PNG
    assert content_type == "image/png"


def test_save_is_content_addressed():
    store = MediaStore()
    assert store.save(_SVG) == store.save(_SVG)


def test_unsupported_payload_rejected():
    with pytest.raises(ValueError):
        MediaStore().save(b"not an image")


def test_traversal_and_bad_names_rejected():
    store = MediaStore()
    for bad in ("../etc/passwd", "a/b.svg", "ABC.svg", "x" * 64 + ".svg",
                "x" * 64 + ".gif", ""):
        assert store.read(bad) is None


def test_unknown_name_reads_none():
    store = MediaStore()
    assert store.read("a" * 64 + ".svg") is None


def test_trailing_newline_name_rejected():
    store = MediaStore()
    assert store.read("a" * 64 + ".svg
") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_media.py -v -k "save or read or traversal or unsupported or unknown"`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.media'`

- [ ] **Step 3: Implement `server/media.py`**

```python
"""Media store: content-addressed image bytes for generated nodes.

The event log only ever holds the *reference* (node meta key "image" ->
"<sha256>.<ext>"); bytes live here. Disk-backed when rooted at a
directory (`<log>.media/` for a persisted log), in-memory otherwise
(tests, ephemeral runs). The name regex makes path traversal impossible
by construction.
"""
from __future__ import annotations

import hashlib
import os
import re

_NAME_RE = re.compile(r"^[a-f0-9]{64}\.(svg|png)$")
_CONTENT_TYPES = {"svg": "image/svg+xml", "png": "image/png"}


def _ext_of(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:512].lstrip().startswith((b"<svg", b"<?xml")):
        return "svg"
    raise ValueError("unsupported image payload (expected PNG or SVG)")


class MediaStore:
    def __init__(self, root: str | None = None):
        self.root = root
        self._mem: dict[str, bytes] = {}

    def save(self, data: bytes) -> str:
        """Store bytes, return their content-addressed name."""
        name = f"{hashlib.sha256(data).hexdigest()}.{_ext_of(data)}"
        if self.root is None:
            self._mem[name] = data
        else:
            os.makedirs(self.root, exist_ok=True)
            path = os.path.join(self.root, name)
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(data)
        return name

    def read(self, name: str) -> tuple[bytes, str] | None:
        """(bytes, content_type), or None for unknown/invalid names."""
        m = _NAME_RE.fullmatch(name)
        if not m:
            return None
        content_type = _CONTENT_TYPES[m.group(1)]
        if self.root is None:
            data = self._mem.get(name)
            return (data, content_type) if data is not None else None
        path = os.path.join(self.root, name)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read(), content_type
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_media.py -v`
Expected: 11 passed (4 image-model + 7 media-store)

- [ ] **Step 5: Commit**

```bash
git add server/media.py tests/test_media.py
git commit -m "server: content-addressed MediaStore"
```

---

## Task 4: `/media/{name}` endpoint

**Files:**
- Modify: `server/app.py` (imports, `create_app` signature, new endpoint)
- Test: `tests/test_media.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_media.py`:

```python
from fastapi.testclient import TestClient

from core import EventStore
from server import StrategyRegistry, create_app


def test_media_endpoint_roundtrip():
    media = MediaStore()
    name = media.save(_SVG)
    client = TestClient(create_app(EventStore(), StrategyRegistry(), media=media))
    r = client.get(f"/media/{name}")
    assert r.status_code == 200
    assert r.content == _SVG
    assert r.headers["content-type"] == "image/svg+xml"


def test_media_endpoint_404s():
    client = TestClient(create_app(EventStore(), StrategyRegistry()))
    assert client.get("/media/" + "a" * 64 + ".svg").status_code == 404
    assert client.get("/media/not-a-hash.svg").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_media.py -v -k endpoint`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'media'`

- [ ] **Step 3: Implement the endpoint**

In `server/app.py`:

1. Change the imports (line 21-23 area) to add `Response` and `MediaStore`:

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
```

and with the relative imports:

```python
from .context import StoreBranchContext
from .media import MediaStore
from .registry import StrategyRegistry
from .settings import SettingsStore, build_model
```

2. Change `create_app` (line 116-119) to accept a media store:

```python
def create_app(store: EventStore, registry: StrategyRegistry,
               settings: SettingsStore | None = None,
               *, media: MediaStore | None = None) -> FastAPI:
    app = FastAPI(title="forklore")
    settings = settings or SettingsStore()
    media = media or MediaStore()
```

3. Add the endpoint inside `create_app`, right after the `strategies()` route (line 286-288):

```python
    @app.get("/media/{name}")
    def get_media(name: str):
        """Serve content-addressed image bytes. Not store-touching, so a
        plain 404 HTTPException is fine here."""
        found = media.read(name)
        if found is None:
            raise HTTPException(404, f"unknown media {name!r}")
        data, content_type = found
        return Response(content=data, media_type=content_type)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_media.py -v`
Expected: 12 passed

Run the full suite to confirm nothing broke: `python -m pytest`
Expected: all pass (115 total: 96 existing + 19 new)

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_media.py
git commit -m "server: GET /media/{name} endpoint"
```

---

## Task 5: `plan_node` meta/image extension

**Files:**
- Modify: `server/context.py` (`plan_node`)
- Modify: `server/app.py` (planned-commit block)
- Modify: `strategies/__init__.py` (`BranchContext` protocol signature)
- Test: `tests/test_crossroads.py` (commit-shape tests live here; they use a stub strategy)

> **Implementation notes (landed):** two integration fixes were required
> beyond the original file list: (a) the SSE `story_events` filter uses
> `isinstance(e, E.Append)` — `SetMetaPayload` has no `.scope`; (b)
> `core/undo.py`'s SetMeta compensation uses `pre.nodes.get(p.node)` with
> `before = None` for nodes created in the same tx (a latent crash newly
> reachable once Append+SetMeta share a tx; no behavior change otherwise).
> The `BranchContext` protocol gained the same kwargs; the meta key
> `"image"` is reserved; a bad-image payload yields an SSE `error` event
> (covered by `test_bad_image_payload_yields_sse_error`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crossroads.py`:

```python
import asyncio
import json

from fastapi.testclient import TestClient

from core import EventStore
from server import StrategyRegistry, create_app


def parse_sse(body: str):
    out = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        out.append((lines[0].removeprefix("event: "),
                    json.loads(lines[1].removeprefix("data: "))))
    return out


class _StubStrategy:
    """Plans fixed nodes; lets us drive the planned-commit path directly."""

    id = "stub/0.1"
    model_name = "stub"

    def __init__(self, planned):
        self._planned = planned

    async def generate(self, ctx, params):
        for kw in self._planned:
            ctx.plan_node(**kw)
        yield "done"


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'


def _stub_client(planned):
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(_StubStrategy(planned))
    return TestClient(create_app(store, registry))


def test_planned_node_carries_meta_and_image():
    client = _stub_client([dict(
        content="A passage.", scope="story",
        meta={"kind": "test"}, image=_SVG)])
    events = parse_sse(client.post("/generate", json={
        "leaf": "A", "strategy": "stub/0.1"}).text)
    committed = next(d for k, d in events if k == "committed")["events"]
    kinds = [e["kind"] for e in committed]
    assert kinds == ["append", "set_meta", "set_meta"]
    assert len({e["tx"] for e in committed}) == 1
    node_id = committed[0]["payload"]["node"]
    meta_events = {e["payload"]["key"]: e["payload"]["value"]
                   for e in committed if e["kind"] == "set_meta"}
    assert meta_events["kind"] == "test"
    ref = meta_events["image"]
    r = client.get(f"/media/{ref}")
    assert r.status_code == 200 and r.content == _SVG
    state = client.get("/state").json()
    assert state["nodes"][node_id]["meta"]["image"] == ref
    assert state["nodes"][node_id]["meta"]["kind"] == "test"


def test_planned_node_without_image_stays_plain():
    client = _stub_client([dict(content="Plain.", scope="story")])
    events = parse_sse(client.post("/generate", json={
        "leaf": "A", "strategy": "stub/0.1"}).text)
    committed = next(d for k, d in events if k == "committed")["events"]
    assert [e["kind"] for e in committed] == ["append"]


def test_undo_reverts_node_and_image_meta():
    client = _stub_client([dict(content="A passage.", scope="story", image=_SVG)])
    events = parse_sse(client.post("/generate", json={
        "leaf": "A", "strategy": "stub/0.1"}).text)
    committed = next(d for k, d in events if k == "committed")["events"]
    tx = committed[0]["tx"]
    node_id = committed[0]["payload"]["node"]
    client.post(f"/undo/{tx}")
    node = client.get("/state").json()["nodes"][node_id]
    assert node["tombstoned"]
    assert node["meta"].get("image") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_crossroads.py -v -k "planned or undo"`
Expected: FAIL — `TypeError: plan_node() got an unexpected keyword argument 'meta'` (and `image`)

- [ ] **Step 3a: Extend `plan_node` in `server/context.py`**

Replace the `plan_node` method (lines 41-49) with:

```python
    def plan_node(self, *, content: str, scope: str = "story",
                  author: str | None = None,
                  meta_extra: dict | None = None,
                  meta: dict | None = None,
                  image: bytes | None = None) -> None:
        """Declare a node to be committed under the leaf on completion
        (the server commits all planned nodes in one transaction).
        `meta` becomes node meta via SetMeta events in the same tx;
        `image` is saved to the media store and referenced as meta
        "image" in the same tx."""
        self.planned.append({
            "content": content, "scope": scope,
            "author": author, "meta_extra": meta_extra or {},
            "meta": meta or {}, "image": image,
        })
```

- [ ] **Step 3b: Extend the planned-commit block in `server/app.py`**

Replace the planned-commit block (lines 379-396, the `if ctx.planned:` branch) with:

```python
                if ctx.planned:
                    # strategy declared its nodes: commit all in ONE tx,
                    # with declared node meta and image refs as SetMeta
                    # events in the same transaction
                    planned_events: list = []
                    for p in ctx.planned:
                        node_id = uuid4().hex
                        planned_events.append(E.Append(payload=E.AppendPayload(
                            node=node_id, parent=req.leaf,
                            author=p["author"] or req.author,
                            content=p["content"], scope=p["scope"],
                            meta=GenerationMeta(
                                strategy=strategy.id,
                                model=getattr(strategy, "model_name",
                                              "unknown"),
                                params={**req.params, **p["meta_extra"]},
                                watermark=ctx.watermark,
                                context_hash=ctx_hash))))
                        for key, value in p["meta"].items():
                            planned_events.append(E.SetMeta(
                                payload=E.SetMetaPayload(
                                    node=node_id, key=key, value=value)))
                        if p["image"] is not None:
                            planned_events.append(E.SetMeta(
                                payload=E.SetMetaPayload(
                                    node=node_id, key="image",
                                    value=media.save(p["image"]))))
                    committed = store.commit(
                        planned_events, actor=f"strategy:{strategy.id}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_crossroads.py -v`
Expected: 10 passed

Run the full suite (best_of_n / beat_planner exercise the old plan_node path):
`python -m pytest`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add server/context.py server/app.py tests/test_crossroads.py
git commit -m "server: plan_node meta/image — SetMeta committed in the generation tx"
```

---

## Task 6: `ctx.steering()` accessor

**Files:**
- Modify: `server/context.py`
- Test: `tests/test_steering.py`

Note: this is the minimal read-path required by the crossroads amendment.
The insert endpoint and projections belong to the steering/world-state spec
(`docs/superpowers/specs/2026-10-17-steering-and-world-state-design.md`) and
are NOT built here.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_steering.py`:

```python
"""ctx.steering(): pin-resolved steering briefs on the branch path.
(The steering insert endpoint and projections land with the
steering/world-state spec; this file covers the read path only.)"""
import pytest

from core import EventStore
from server.context import StoreBranchContext


@pytest.fixture
def store():
    s = EventStore()
    s.append_node(None, "you", "The door was open.", node_id="A")
    s.append_node("A", "you", "Tone: gothic, quiet, exact.",
                  node_id="S", scope="workspace")
    s.set_meta("S", "kind", "steering")
    s.append_node("S", "character", "She stepped inside.", node_id="B")
    return s


def test_steering_returns_briefs_root_to_leaf(store):
    briefs = StoreBranchContext(store, "B").steering()
    assert [m.node for m in briefs] == ["S"]
    assert briefs[0].content == "Tone: gothic, quiet, exact."


def test_steering_empty_when_absent(store):
    assert StoreBranchContext(store, "A").steering() == []


def test_steering_briefs_stay_out_of_story_materialization(store):
    msgs = store.materialize("B")
    assert [m.content for m in msgs] == [
        "The door was open.", "She stepped inside."]


def test_steering_sees_revised_version(store):
    store.revise("S", "Tone: bright, wry, fast.")
    briefs = StoreBranchContext(store, "B").steering()
    assert briefs[0].content == "Tone: bright, wry, fast."
    assert briefs[0].version == 2


def test_steering_respects_pins(store):
    store.revise("S", "Tone: bright, wry, fast.")
    store.pin("S", "B", 1)
    briefs = StoreBranchContext(store, "B").steering()
    assert briefs[0].content == "Tone: gothic, quiet, exact."
    assert briefs[0].pinned
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_steering.py -v`
Expected: FAIL — `AttributeError: 'StoreBranchContext' object has no attribute 'steering'`

- [ ] **Step 3: Implement `steering()`**

Append to `StoreBranchContext` in `server/context.py`:

```python
    def steering(self) -> list[Message]:
        """Steering briefs (kind="steering" meta) on the path to the
        leaf, root -> leaf, with pin-resolved versions. Empty when the
        branch has none. Strategies decide whether/how to include them
        in the prompt; record_prompt keeps context_hash honest."""
        state = self._store.state()
        return [
            m for m in self._store.materialize(
                self.leaf, include_workspace=True)
            if state.nodes[m.node].meta.get("kind") == "steering"
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_steering.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add server/context.py tests/test_steering.py
git commit -m "server: ctx.steering() read-path accessor"
```

---

## Task 7: `guide` meta-role

**Files:**
- Modify: `strategies/models.py` (`LiteLLMModel.role_of`, `DemoModel`)
- Test: `tests/test_crossroads.py`

Per AGENTS.md, new meta-role voices must be added to
`LiteLLMModel.role_of` AND the `DemoModel` reply cycle.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crossroads.py`:

```python
from core.materialize import Message
from strategies.models import DemoModel, LiteLLMModel


def _msg(author, content="x"):
    return Message(node="", author=author, content=content,
                   version=1, pinned=False, scope="story")


def test_guide_maps_to_user_role():
    assert LiteLLMModel.role_of("guide") == "user"


def test_demo_model_answers_guide_turns_in_crossroads_format():
    async def run():
        model = DemoModel()
        return "".join([t async for t in model.stream([_msg("guide")], {})])
    text = asyncio.run(run())
    passage, choices = parse_crossroads_output(text)
    assert passage
    assert len(choices) == 3


def test_demo_model_director_cycle_unchanged():
    async def run():
        model = DemoModel()
        return "".join([t async for t in model.stream([_msg("director")], {})])
    text = asyncio.run(run())
    assert "PASSAGE:" not in text  # director stays a direction line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_crossroads.py -v -k "guide or director"`
Expected: FAIL — `test_guide_maps_to_user_role` assertion (`role_of("guide")` returns `"assistant"`); `test_demo_model_answers_guide_turns_in_crossroads_format` gets a narrative reply with no `CHOICES:` block (0 choices).

- [ ] **Step 3: Implement**

In `strategies/models.py`:

1. `LiteLLMModel.role_of` (lines 130-135): add `"guide"` to the meta-role tuple:

```python
        if author in ("you", "user", "director", "critic", "drafter",
                      "planner", "guide"):
            return "user"
```

2. `DemoModel.__init__` (after the `self._beat_sheets` block, line 93): add a guided-turn cycle:

```python
        self._guided = itertools.cycle([
            "PASSAGE:\nMara opened the log to its final page, and the "
            "date at the top was tomorrow's.\n\nCHOICES:\n"
            "- Read the entry aloud\n- Tear out the page\n"
            "- Show it to the blue flame",
            "PASSAGE:\nThe flame dimmed as she approached, folding "
            "itself small, like something hoping not to be seen.\n\n"
            "CHOICES:\n- Cup the flame in her hands\n"
            "- Ask it a question\n- Leave it burning and walk away",
        ])
```

3. `DemoModel.stream` dispatch (lines 98-106): add the guide branch before the else:

```python
        if last == "critic":
            text = next(self._verdicts)
        elif last == "director":
            text = next(self._directions)
        elif last == "planner":
            text = next(self._beat_sheets)
        elif last == "guide":
            text = next(self._guided)
        else:
            text = next(self._replies)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_crossroads.py -v`
Expected: 13 passed

Full suite: `python -m pytest` — all pass (crew/best_of_n demo cycles untouched)

- [ ] **Step 5: Commit**

```bash
git add strategies/models.py tests/test_crossroads.py
git commit -m "models: guide meta-role (role_of + DemoModel crossroads cycle)"
```

---

## Task 8: The `crossroads/0.1` strategy

**Files:**
- Modify: `strategies/crossroads.py` (add the strategy class)
- Test: `tests/test_crossroads.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crossroads.py`:

```python
from strategies.images import DemoImageModel
from strategies.models import ScriptedModel
from server.context import StoreBranchContext

from strategies.crossroads import Crossroads

_REPLY = ("PASSAGE:\nShe stepped inside, and the dust rose to meet her.\n\n"
          "CHOICES:\n- Light a match\n- Call into the dark\n- Take the stairs")


@pytest.fixture
def client():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(ScriptedModel([_REPLY]), DemoImageModel()))
    return TestClient(create_app(store, registry))


def _generate(client, params=None):
    r = client.post("/generate", json={
        "leaf": "A", "strategy": "crossroads/0.1", "author": "character",
        "params": params or {}})
    assert r.status_code == 200
    return parse_sse(r.text)


def test_turn_commits_passage_choices_and_image(client):
    events = _generate(client, {"choice": "Open it", "guidance": "keep it quiet"})
    committed = next(d for k, d in events if k == "committed")["events"]
    story = [e for e in committed
             if e["kind"] == "append" and e["payload"]["scope"] == "story"]
    assert len(story) == 1
    payload = story[0]["payload"]
    assert payload["content"] == "She stepped inside, and the dust rose to meet her."
    params = payload["meta"]["params"]
    assert params["choices"] == ["Light a match", "Call into the dark",
                                 "Take the stairs"]
    assert params["choice"] == "Open it"
    assert params["guidance"] == "keep it quiet"
    assert params["image_prompt"].startswith("She stepped inside")
    assert params["image_prompt"].endswith("— illustration for a story passage")
    # image committed in the same tx, referenced from node meta
    meta_events = [e for e in committed if e["kind"] == "set_meta"
                   and e["payload"]["key"] == "image"]
    assert len(meta_events) == 1
    assert meta_events[0]["tx"] == story[0]["tx"]
    ref = meta_events[0]["payload"]["value"]
    assert client.get(f"/media/{ref}").status_code == 200


def test_passage_is_streamed(client):
    events = _generate(client)
    text = "".join(d["delta"] for k, d in events if k == "token")
    assert text == "She stepped inside, and the dust rose to meet her."


def test_malformed_reply_degrades_to_free_form_only():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(
        ScriptedModel(["A plain reply with no structure at all."]),
        DemoImageModel()))
    client = TestClient(create_app(store, registry))
    events = _generate(client)
    committed = next(d for k, d in events if k == "committed")["events"]
    story = [e for e in committed
             if e["kind"] == "append" and e["payload"]["scope"] == "story"]
    assert story[0]["payload"]["content"] == \
        "A plain reply with no structure at all."
    assert story[0]["payload"]["meta"]["params"]["choices"] == []


def test_image_failure_still_commits_the_node():
    class FailingImage:
        name = "failing"
        ext = "svg"

        async def generate(self, prompt, params):
            raise RuntimeError("provider down")

    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(ScriptedModel([_REPLY]), FailingImage()))
    client = TestClient(create_app(store, registry))
    events = _generate(client)
    committed = next(d for k, d in events if k == "committed")["events"]
    kinds = [e["kind"] for e in committed]
    assert kinds == ["append"]  # no image SetMeta
    assert committed[0]["payload"]["content"].startswith("She stepped inside")
    params = committed[0]["payload"]["meta"]["params"]
    assert params["image_error"] == "provider down"


def test_empty_passage_yields_sse_error():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(
        ScriptedModel(["PASSAGE:

CHOICES:
- a
- b
- c"]),
        DemoImageModel()))
    client = TestClient(create_app(store, registry))
    events = _generate(client)
    kinds = [k for k, _ in events]
    assert "error" in kinds
    assert "committed" not in kinds


def test_guidance_in_prompt_but_never_in_story():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    strategy = Crossroads(ScriptedModel([_REPLY]), DemoImageModel())
    ctx = StoreBranchContext(store, "A")

    async def run():
        return [t async for t in strategy.generate(
            ctx, {"choice": "Open it", "guidance": "keep it quiet"})]

    asyncio.run(run())
    prompt = ctx.prompt or []
    assert prompt[-1].author == "guide"
    assert "keep it quiet" in prompt[-1].content
    assert "Open it" in prompt[-1].content
    assert all("keep it quiet" not in m.content
               for m in store.materialize("A"))


def test_steering_briefs_prepended_as_director_messages():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    store.append_node("A", "you", "Tone: gothic, quiet, exact.",
                      node_id="S", scope="workspace")
    store.set_meta("S", "kind", "steering")
    store.append_node("S", "character", "She stepped inside.", node_id="B")
    strategy = Crossroads(ScriptedModel([_REPLY]), DemoImageModel())
    ctx = StoreBranchContext(store, "B")

    async def run():
        return [t async for t in strategy.generate(ctx, {})]

    asyncio.run(run())
    prompt = ctx.prompt or []
    directors = [m for m in prompt if m.author == "director"]
    assert len(directors) == 1
    assert "Tone: gothic, quiet, exact." in directors[0].content
    assert prompt[-1].author == "guide"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_crossroads.py -v -k "turn or streamed or malformed or image_failure or guidance or steering"`
Expected: FAIL — `ImportError: cannot import name 'Crossroads' from 'strategies.crossroads'`

- [ ] **Step 3: Implement the strategy**

In `strategies/crossroads.py`, extend the imports and append the class:

```python
from typing import Any, AsyncIterator

from core.materialize import Message

from .best_of_n import collect
from .images import DemoImageModel, ImageModel
from .models import ChatModel
from .simple_passthrough import truncate


def _msg(author: str, content: str) -> Message:
    """An unpersisted prompt message (mirrors beat_planner._msg)."""
    return Message(node="", author=author, content=content,
                   version=1, pinned=False, scope="story")


class Crossroads:
    id = "crossroads/0.1"

    def __init__(self, model: ChatModel,
                 image_model: ImageModel | None = None):
        self.model = model
        self.model_name = model.name
        self.image_model = image_model or DemoImageModel()

    def for_model(self, model: ChatModel) -> "Crossroads":
        return Crossroads(model, self.image_model)

    def for_image_model(self, image_model: ImageModel) -> "Crossroads":
        return Crossroads(self.model, image_model)

    async def generate(self, ctx, params: dict[str, Any]) -> AsyncIterator[str]:
        author = params.get("author", "character")
        choice = (params.get("choice") or "").strip()
        guidance = (params.get("guidance") or "").strip()

        prompt = list(truncate(ctx.materialize(), params.get("max_messages")))
        for m in ctx.steering():
            prompt.append(_msg("director",
                               f"Story brief (steering):\n{m.content}"))
        prompt.append(_msg("guide",
            (f"The reader chose: {choice}." if choice else
             "The reader lets you choose the direction.")
            + (f"\nDirection from the author (not part of the story): "
               f"{guidance}" if guidance else "")
            + "\n\nWrite the next passage, then propose three short "
              "choices for what happens next. Reply in exactly this "
              "format:\n\nPASSAGE:\n<the next passage>\n\nCHOICES:\n"
              "- <first choice>\n- <second choice>\n- <third choice>"))
        ctx.record_prompt(prompt)

        raw = await collect(self.model.stream(prompt, {}))
        passage, choices = parse_crossroads_output(raw)
        if not passage:
            # empty passage: fall back to the stripped pre-CHOICES body,
            # never to the raw reply (markers must not enter the story)
            passage = (raw.strip().partition("CHOICES:")[0]
                       .removeprefix("PASSAGE:").strip())
        if not passage:
            raise RuntimeError("crossroads: the model produced no passage")

        words = passage.split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "

        # excerpt first, so the demo plate's title word is passage-specific
        image_prompt = (" ".join(passage.split()[:40])
                        + " — illustration for a story passage")
        image = None
        image_error = None
        try:
            # params are NOT forwarded: routing keys (choice/guidance/
            # author) would leak into provider kwargs
            image = await self.image_model.generate(image_prompt, {})
        except Exception as e:
            image_error = str(e)  # the story is primary; the picture is decoration

        ctx.plan_node(
            content=passage, scope="story", author=author,
            meta_extra={"choices": choices,
                        "choice": choice or None,
                        "guidance": guidance or None,
                        "image_prompt": image_prompt,
                        "image_error": image_error},
            image=image)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_crossroads.py -v`
Expected: 21 passed

Full suite: `python -m pytest` — all pass

- [ ] **Step 5: Commit**

```bash
git add strategies/crossroads.py tests/test_crossroads.py
git commit -m "strategies: crossroads/0.1 — choice-driven play with per-node image"
```

---

## Task 9: `image_model` setting + route rebinding

**Files:**
- Modify: `server/settings.py`
- Modify: `server/app.py` (generate route)
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py` (check the file's existing imports/fixtures and match them):

```python
def test_image_model_setting_roundtrip():
    from server.settings import SettingsStore
    s = SettingsStore()
    assert s.redacted()["image_model"] is None
    s.update({"image_model": "dall-e-3"})
    assert s.redacted()["image_model"] == "dall-e-3"
    s.update({"image_model": ""})  # empty string clears
    assert s.redacted()["image_model"] is None


def test_build_image_model_unconfigured_is_none():
    from server.settings import SettingsStore, build_image_model
    assert build_image_model(SettingsStore()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_settings.py -v -k image`
Expected: FAIL — `KeyError`/`AssertionError` on `image_model`, and `ImportError: cannot import name 'build_image_model'`

- [ ] **Step 3: Implement**

In `server/settings.py`:

1. Add to `_DEFAULTS` (line 30-35):

```python
_DEFAULTS: dict[str, Any] = {
    "default_model": None,
    "image_model": None,     # litellm image model for generated-node art
    "api_keys": {},          # provider -> secret
    "strategy_models": {},   # strategy id -> litellm model string
    "default_params": {},    # temperature, max_tokens, ... merged into calls
}
```

2. In `update()` (after the `default_model` block, line 85-86), add:

```python
            if "image_model" in patch:
                self._data["image_model"] = patch["image_model"] or None
```

3. Append `build_image_model` at the end of the file:

```python
def build_image_model(settings: SettingsStore):
    """Instantiate the configured image model, or None when unconfigured
    (strategies then keep their built-in demo image model). Raises
    RuntimeError with a clear message when litellm is absent."""
    model = settings.get()["image_model"]
    if not model:
        return None
    try:
        from strategies.images import LiteLLMImageModel
        kwargs: dict[str, Any] = {}
        key = settings.api_key_for(model)
        if key:
            kwargs["api_key"] = key
        return LiteLLMImageModel(model, **kwargs)
    except ImportError:
        raise RuntimeError(
            f"image model {model!r} requires litellm "
            f"(pip install litellm), which is not installed here")
```

In `server/app.py`:

4. Extend the settings import (line 32):

```python
from .settings import SettingsStore, build_image_model, build_model
```

5. Allow `image_model` in PUT /settings (line 301-302):

```python
        unknown = set(patch) - {"default_model", "image_model", "api_keys",
                                "strategy_models", "default_params"}
```

6. In the `generate` route, after the chat-model rebinding block (after line 365's `return StreamingResponse(...)` for the failure case), add image-model rebinding:

```python
        if hasattr(strategy, "for_image_model"):
            try:
                image_model = build_image_model(settings)
                if image_model:
                    strategy = strategy.for_image_model(image_model)
            except RuntimeError as e:
                message = str(e)  # `e` is cleared after the except block

                async def failed_image() -> AsyncIterator[str]:
                    yield sse("error", {"message": message})
                return StreamingResponse(failed_image(),
                                         media_type="text/event-stream")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_settings.py tests/test_server.py -v`
Expected: all pass (including the 2 new image settings tests)

Full suite: `python -m pytest`

- [ ] **Step 5: Commit**

```bash
git add server/settings.py server/app.py tests/test_settings.py
git commit -m "server: image_model setting + per-request image model rebinding"
```

---

## Task 10: Server wiring + docs

**Files:**
- Modify: `server/__main__.py`
- Modify: `.gitignore`
- Modify: `docs/architecture.md`
- Modify: `README.md`

- [ ] **Step 1: Wire crossroads and the media dir in `server/__main__.py`**

Replace the imports block (lines 16-19) with:

```python
from core import EventStore
from strategies.beat_planner import BeatPlanner
from strategies.best_of_n import BestOfN
from strategies.crossroads import Crossroads
from strategies.images import DemoImageModel
from strategies.models import DemoModel, FakeModel
from strategies.simple_passthrough import SimplePassthrough
```

Add the media import with the other relative imports (after line 21):

```python
from .app import create_app
from .demo import seed_demo
from .media import MediaStore
from .registry import StrategyRegistry
from .settings import SettingsStore
```

Register the strategy and wire the media store — replace lines 39-49 area:

```python
registry.register(BeatPlanner(DemoModel()))
registry.register(Crossroads(DemoModel(), DemoImageModel()))
```

and

```python
_media_path = (_db_path + ".media") if _db_path else "forklore.media"
app = create_app(store, registry, settings, media=MediaStore(_media_path))
```

(replacing the current `app = create_app(store, registry, settings)` line).

- [ ] **Step 2: Ignore media dirs**

Append to `.gitignore`:

```
*.media/
forklore.media/
```

- [ ] **Step 3: Update the strategy lists in docs**

In `docs/architecture.md`, replace the strategy list (lines 87-91) with:

```markdown
Current strategies: `simple_passthrough/0.1` (baseline),
`best_of_n/0.1` (parallel sampling + critic scoring),
`langgraph_crew/0.1` (sequential pipeline with a revision loop),
`beat_planner/0.1` (hierarchical plan-then-execute; the roadmap is a
workspace node, progress is derived from the tree, not strategy memory),
`crossroads/0.1` (choice-driven play: one passage + three choices per
turn, a `guide` meta-role instruction turn carrying the reader's choice
and one-shot hidden guidance, and a per-node generated image committed
as node meta in the same transaction).
```

In `README.md`, add after the `beat_planner/0.1` bullet (line 47-48):

```markdown
- `crossroads/0.1` — choice-driven play: a passage plus three choices per
  turn, a free-form choice, a hidden one-shot guidance box, and a
  generated image per node (demo image model works offline).
```

- [ ] **Step 4: Smoke-test the server**

```bash
python -m pytest                       # full suite stays green
python -m server /tmp/xroads-smoke.db  # boots; Ctrl-C after the startup line
```

Expected: server starts without errors; the "note: langgraph not installed" line may appear.

- [ ] **Step 5: Commit**

```bash
git add server/__main__.py .gitignore docs/architecture.md README.md
git commit -m "server: register crossroads, wire media dir; docs list the new strategy"
```

---

## Task 11: Client store + types

**Files:**
- Modify: `web/src/lib/store.ts`

No type changes needed: `TNode.meta` is already `Record<string, unknown>`
and `GenMeta.params` is already `Record<string, unknown>`.

- [ ] **Step 1: Extend the store interface**

In `web/src/lib/store.ts`, `ForkloreState` interface:

1. Change the generate signature (line 97):

```typescript
  generate: (leaf?: string, params?: Record<string, unknown>) => Promise<void>
```

2. Add play-mode state (next to `compare: ... | null`, line 87):

```typescript
  play: boolean
  setPlay: (on: boolean) => void
```

- [ ] **Step 2: Implement**

1. Initial state (near line 142, `compare: null,`):

```typescript
    play: false,
```

2. Replace the `generate` action (lines 221-252) with:

```typescript
    async generate(leaf, params) {
      const { cursor, strategy, streaming } = get()
      const target = leaf ?? cursor
      if (!target || !strategy || streaming.active) return
      set({ streaming: { active: true, text: '' }, error: null })
      let committed: any = null
      try {
        for await (const ev of ssePost('/generate', {
          leaf: target,
          strategy,
          author: 'character',
          ...(params && Object.keys(params).length ? { params } : {}),
        })) {
          if (ev.event === 'token') {
            set((s) => ({
              streaming: { active: true, text: s.streaming.text + ev.data.delta },
            }))
          } else if (ev.event === 'committed') {
            committed = ev.data.event
          } else if (ev.event === 'error') {
            set({ error: ev.data.message })
          }
        }
      } catch (e) {
        set({ error: String(e) })
      }
      set({ streaming: { active: false, text: '' } })
      if (committed) {
        pushUndo(committed.tx)
        set({ cursor: committed.payload.node })
        await get().refresh()
      }
    },
```

3. Add the `setPlay` action (next to `setCompare`, line 400):

```typescript
    setPlay: (on) => set({ play: on }),
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npm run build`
Expected: builds clean (existing callers `generate()` / `generate(parent)` still typecheck)

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/store.ts
git commit -m "web: generate accepts params; play-mode flag"
```

---

## Task 12: PlayPane component

**Files:**
- Create: `web/src/components/PlayPane.tsx`

- [ ] **Step 1: Write the component**

Create `web/src/components/PlayPane.tsx`:

```tsx
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

function Passage({ nodeId, content }: { nodeId: string; content: string }) {
  const proj = useForklore((s) => s.proj)
  const img = imageOf(proj?.nodes[nodeId])
  return (
    <article className="msg">
      {img && (
        <img
          className="mb-3 w-full max-w-[40rem] border border-border"
          src={`/media/${img}`}
          alt="illustration for this passage"
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

  const choose = (choice?: string) => {
    const params: Record<string, unknown> = {}
    if (choice?.trim()) params.choice = choice.trim()
    if (guidance.trim()) params.guidance = guidance.trim()
    void generate(undefined, params)
    setFreeform('')
    setGuidance('')
  }

  return (
    <div className="shrink-0 border-t border-border px-8 py-4">
      <div className="mx-auto max-w-[70ch] space-y-3">
        {choices.length > 0 && (
          <div className="flex flex-col gap-2">
            {choices.map((c) => (
              <Button
                key={c}
                variant="outline"
                size="sm"
                className="justify-start font-serif italic"
                disabled={streaming || strategy !== 'crossroads/0.1'}
                onClick={() => choose(c)}
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
            value={freeform}
            disabled={streaming}
            onChange={(e) => setFreeform(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && freeform.trim()) choose(freeform)
            }}
          />
          <Button
            size="sm"
            className="gap-1.5"
            disabled={streaming || !freeform.trim()}
            onClick={() => choose(freeform)}
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
              <Passage key={m.node} nodeId={m.node} content={m.content} />
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
```

Note: choice buttons are disabled unless the selected strategy is
`crossroads/0.1` — other strategies don't supply `choices` params, and the
free-form box still works with any strategy (it just passes `choice`).

- [ ] **Step 2: Typecheck**

Run: `cd web && npm run build`
Expected: compiles (PlayPane not imported anywhere yet — that's fine; tsc -b checks all of src)

- [ ] **Step 3: Commit**

```bash
git add web/src/components/PlayPane.tsx
git commit -m "web: PlayPane — passage scroll, images, choices, guidance box"
```

---

## Task 13: Wire the Play view into the app

**Files:**
- Modify: `web/src/components/TopBar.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/vite.config.ts`

- [ ] **Step 1: Add the Play/Workbench toggle to TopBar**

In `web/src/components/TopBar.tsx`:

1. Add store hooks (after line 18, `const branchCount = ...`):

```tsx
  const play = useForklore((s) => s.play)
  const setPlay = useForklore((s) => s.setPlay)
```

2. Insert the toggle right after the strategy `select` block (after line 50's closing `)}`):

```tsx
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
```

- [ ] **Step 2: Render PlayPane in App**

In `web/src/App.tsx`:

1. Add the import (after line 4):

```tsx
import PlayPane from '@/components/PlayPane'
```

2. Add the hook (after line 12, `const comparing = ...`):

```tsx
  const play = useForklore((s) => s.play)
```

3. Change the pane selection (line 25) to:

```tsx
        {comparing ? <ComparePane /> : play ? <PlayPane /> : <ChatPane />}
```

- [ ] **Step 3: Proxy `/media` in dev**

In `web/vite.config.ts`, add `'/media'` to the proxy list (line 14):

```typescript
      ['/nodes', '/state', '/branch', '/events', '/strategies', '/generate', '/undo', '/media']
```

- [ ] **Step 4: Choice provenance chip in the workbench**

Per the spec ("choice labels render as provenance chips on node headers,
like score/rounds/beat today"), in `web/src/components/ChatPane.tsx`'s
`MessageItem` header, insert after the score chip block (after line 101's
`)}`):

```tsx
        {node.generation?.params?.choice != null && (
          <span
            className="chip"
            title="the reader's choice that led to this passage"
          >
            chose: {String(node.generation.params.choice)}
          </span>
        )}
```

- [ ] **Step 5: Build and lint**

Run: `cd web && npm run build && npm run lint`
Expected: both pass

- [ ] **Step 6: Commit**

```bash
git add web/src/components/TopBar.tsx web/src/App.tsx web/vite.config.ts web/src/components/ChatPane.tsx
git commit -m "web: play/workbench toggle; /media dev proxy; choice provenance chip"
```

---

## Task 14: End-to-end verification

- [ ] **Step 1: Full backend suite**

Run: `python -m pytest`
Expected: all pass (137 total: 96 original + 21 crossroads + 13 media +
5 steering + 2 image settings)

- [ ] **Step 2: Full frontend checks**

Run: `cd web && npm run build && npm run lint`
Expected: both pass

- [ ] **Step 3: Live smoke test with the demo models**

```bash
python -m server /tmp/xroads-e2e.db &
sleep 2
# find the demo story leaf
LEAF=$(curl -s localhost:8000/state | python -c "
import json, sys
s = json.load(sys.stdin)
kids = {n['id'] for n in s['nodes'].values()}
children = {c for n in s['nodes'].values() for c in n['children']}
leaves = [n for n in s['nodes'].values()
          if n['scope'] == 'story' and not n['tombstoned']
          and not [c for c in n['children'] if c in children]]
print(sorted(leaves, key=lambda n: -n['created_seq'])[0]['id'])")
# generate a crossroads turn with a choice and hidden guidance
curl -s -N -X POST localhost:8000/generate -H 'Content-Type: application/json' \
  -d "{\"leaf\": \"$LEAF\", \"strategy\": \"crossroads/0.1\", \"params\": {\"choice\": \"Open the log\", \"guidance\": \"keep it quiet\"}}" \
  > /tmp/xroads-turn.out
grep -A1 'event: committed' /tmp/xroads-turn.out
```

Expected: the `committed` payload contains an `append` (scope `story`)
whose `meta.params` has `choices` (3), `choice: "Open the log"`,
`guidance: "keep it quiet"`, plus a `set_meta` event with key `image`
— all sharing one `tx`. Then verify the media serving:

```bash
REF=$(python -c "
import json
for block in open('/tmp/xroads-turn.out').read().strip().split('\n\n'):
    lines = block.split('\n')
    if lines[0] == 'event: committed':
        d = json.loads(lines[1].removeprefix('data: '))
        print(next(e['payload']['value'] for e in d['events']
                   if e['kind'] == 'set_meta'
                   and e['payload']['key'] == 'image'))
")
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' "localhost:8000/media/$REF"
```

Expected: `200 image/svg+xml`

```bash
# UI check: cd web && npm run dev, open http://localhost:3000, pick
# "crossroads/0.1" in the strategy select, click "play": passages show
# images, the leaf shows three choice buttons + free-form + guidance.
kill %1
```

- [ ] **Step 4: Final commit (if any fixes were needed)**

```bash
git status   # should be clean; commit any verification fixes
```

---

## Spec coverage map

| Spec requirement | Task |
|---|---|
| Turn cycle: `/generate` with choice/guidance params | 8, 11 |
| One tx: story Append + image SetMeta | 5, 8 |
| `choices` in GenerationMeta.params | 8 |
| Guidance hidden from story, recorded in params | 8 (tests) |
| Steering read-path (amendment) | 6, 8 |
| `ImageModel` protocol + DemoImageModel + provider model | 2 |
| Image bytes in media dir, referenced via SetMeta | 3, 5 |
| `plan_node` meta param | 5 |
| `/media/{name}` with traversal guard | 3, 4 |
| Image failure degrades, never kills text | 8 |
| Malformed output degrades (empty choices) | 1, 8 |
| Image provider via settings | 9 |
| Demo offline loop (DemoModel choices cycle) | 7 |
| Play view: passages, images, choices, free-form, guidance | 12, 13 |
| Choice provenance chip in the workbench | 13 |
| `tests/test_core.py` untouched | (no core changes) |
| Docs updated (architecture.md, README) | 10 |
