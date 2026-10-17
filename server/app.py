"""forklore server: FastAPI + SSE over the core event log.

Design rules carried over from the architecture discussion:

  * one HTTP request -> one store transaction (one undo unit);
  * the server owns fold/materialize -- clients render projections and
    never reimplement core semantics;
  * SSE for token streams during generation, and for log replication
    ("events after seq N", resumable via the client's last seen seq --
    Last-Event-ID maps directly onto seq);
  * on generation completion the server commits the Append with
    GenerationMeta (strategy id, model, params, watermark, context_hash).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from core import EventStore, GenerationMeta, InvariantError
from core import events as E
from core.materialize import hash_messages
from core.state import Node, TreeState

from .context import StoreBranchContext
from .media import MediaStore
from .registry import StrategyRegistry
from .settings import SettingsStore, build_model


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------

def event_dict(ev) -> dict[str, Any]:
    return ev.model_dump(mode="json")


def node_dict(n: Node) -> dict[str, Any]:
    return {
        "id": n.id, "parent": n.parent, "author": n.author, "scope": n.scope,
        "created_seq": n.created_seq, "version": n.version,
        "content": n.content, "versions": list(n.versions),
        "tombstoned": n.tombstoned, "meta": n.meta, "children": n.children,
        "last_revise_seq": n.last_revise_seq, "tx": n.tx,
        "generation": n.generation.model_dump() if n.generation else None,
    }


def state_dict(s: TreeState) -> dict[str, Any]:
    return {
        "head": s.head, "root": s.root,
        "nodes": {nid: node_dict(n) for nid, n in s.nodes.items()},
        "pins": {f"{n}|{c}": v for (n, c), v in s.pins.items()},
    }


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------

class AppendReq(BaseModel):
    parent: str | None
    author: str
    content: str
    scope: str = "story"


class ReviseReq(BaseModel):
    content: str


class DivergeReq(BaseModel):
    content: str
    move: str


class VariantReq(BaseModel):
    content: str


class PinReq(BaseModel):
    via_child: str
    version: int


class SetMetaReq(BaseModel):
    key: str
    value: Any = None


class GenerateReq(BaseModel):
    leaf: str
    strategy: str
    author: str = "assistant"
    params: dict[str, Any] = {}


class BranchReq(BaseModel):
    name: str
    node: str


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

def create_app(store: EventStore, registry: StrategyRegistry,
               settings: SettingsStore | None = None,
               *, media: MediaStore | None = None) -> FastAPI:
    app = FastAPI(title="forklore")
    settings = settings or SettingsStore()
    media = media or MediaStore()

    def guard(fn):
        try:
            return fn()
        except KeyError as e:
            raise HTTPException(404, str(e))
        except InvariantError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(404, str(e))

    # -- writes (one request = one tx) -------------------------------------

    @app.post("/nodes")
    def append(req: AppendReq):
        ev = guard(lambda: store.append_node(
            req.parent, req.author, req.content, scope=req.scope))
        return event_dict(ev)

    @app.post("/nodes/{node}/revise")
    def revise(node: str, req: ReviseReq):
        return event_dict(guard(lambda: store.revise(node, req.content)))

    @app.post("/nodes/{node}/diverge")
    def diverge(node: str, req: DivergeReq):
        v, g = guard(lambda: store.diverge(node, req.content, req.move))
        return {"variant": event_dict(v), "graft": event_dict(g)}

    @app.post("/nodes/{node}/variant")
    def variant(node: str, req: VariantReq):
        """Edited sibling without moving any subtree (used when the node
        is a leaf: there is nothing downstream to graft)."""
        return event_dict(guard(lambda: store.variant(node, req.content)))

    @app.post("/nodes/{node}/tombstone")
    def tombstone(node: str):
        return event_dict(guard(lambda: store.tombstone(node)))

    @app.post("/nodes/{node}/restore")
    def restore(node: str):
        return event_dict(guard(lambda: store.restore(node)))

    @app.post("/nodes/{node}/pin")
    def pin(node: str, req: PinReq):
        return event_dict(guard(lambda: store.pin(node, req.via_child,
                                                  req.version)))

    @app.post("/nodes/{node}/unpin")
    def unpin(node: str, req: PinReq):
        return event_dict(guard(lambda: store.unpin(node, req.via_child)))

    @app.post("/nodes/{node}/meta")
    def set_meta(node: str, req: SetMetaReq):
        return event_dict(guard(lambda: store.set_meta(node, req.key,
                                                       req.value)))

    @app.post("/undo/{tx}")
    def undo(tx: str):
        return [event_dict(e) for e in guard(lambda: store.undo(tx))]

    # -- branches (named storylines, via SetMeta -- no schema change) --------

    def find_branch(s: TreeState, name: str) -> Node | None:
        return next((n for n in s.nodes.values()
                     if n.meta.get("branch") == name), None)

    @app.get("/branches")
    def list_branches():
        s = store.state()
        return sorted(
            ({"name": n.meta["branch"], "node": n.id,
              "created_seq": n.created_seq,
              "depth": len(s.path_to_root(n.id)),
              "hidden": s.is_hidden(n.id)}
             for n in s.nodes.values() if n.meta.get("branch")),
            key=lambda b: b["created_seq"])

    @app.post("/branches")
    def name_branch(req: BranchReq):
        """Name the storyline at a node. Naming an already-named node is a
        rename; names must be unique across the tree."""
        s = store.state()
        if req.node not in s.nodes:
            raise HTTPException(404, f"unknown node {req.node!r}")
        name = req.name.strip()
        if not name:
            raise HTTPException(422, "branch name must not be empty")
        clash = find_branch(s, name)
        if clash and clash.id != req.node:
            raise HTTPException(409, f"branch name {name!r} already taken")
        return event_dict(guard(lambda: store.set_meta(
            req.node, "branch", name)))

    @app.delete("/branches/{name}")
    def unname_branch(name: str):
        s = store.state()
        node = find_branch(s, name)
        if not node:
            raise HTTPException(404, f"unknown branch {name!r}")
        return event_dict(guard(lambda: store.set_meta(
            node.id, "branch", None)))

    @app.post("/branches/{name}/prune")
    def prune_branch(name: str):
        """Tombstone the named node (hiding its subtree) and drop the name
        -- ONE transaction, so the prune is a single undo unit. Nothing is
        deleted: undo or restore brings the storyline back."""
        s = store.state()
        node = find_branch(s, name)
        if not node:
            raise HTTPException(404, f"unknown branch {name!r}")
        events: list = []
        if not node.tombstoned:
            events.append(E.Tombstone(payload=E.NodePayload(node=node.id)))
        events.append(E.SetMeta(payload=E.SetMetaPayload(
            node=node.id, key="branch", value=None)))
        return [event_dict(e) for e in guard(lambda: store.commit(events))]

    # -- reads ----------------------------------------------------------------

    @app.get("/state")
    def state(at: int | None = None):
        return state_dict(store.state(at))

    @app.get("/branch/{leaf}")
    def branch(leaf: str, at: int | None = None):
        s = store.state(at)
        if leaf not in s.nodes:
            raise HTTPException(404, f"unknown node {leaf!r}")
        msgs = store.materialize(leaf, at=at)
        return {
            "messages": [m.__dict__ for m in msgs],
            "stale": [] if at is not None else store.stale(leaf),
        }

    @app.get("/workspace/{node}")
    def workspace(node: str, at: int | None = None):
        """The workspace artifacts (rejected candidates, drafts, critiques)
        produced by the same generation run as the given node: every
        workspace-scope node sharing its transaction, in execution order.
        A beat_planner story node additionally shows the roadmap it
        followed, even though the plan was committed in an earlier tx."""
        s = store.state(at)
        if node not in s.nodes:
            raise HTTPException(404, f"unknown node {node!r}")
        src = s.nodes[node]
        artifacts = [n for n in s.nodes.values()
                     if n.tx == src.tx and n.scope == "workspace"
                     and n.id != node]
        plan_id = (src.generation.params.get("plan_id")
                   if src.generation else None)
        if plan_id and all((n.generation.params.get("plan_id") != plan_id
                            if n.generation else True) for n in artifacts):
            plan = next((n for n in s.nodes.values()
                         if n.scope == "workspace" and n.generation
                         and n.generation.params.get("plan_id") == plan_id
                         and not s.is_hidden(n.id)), None)
            if plan:
                artifacts.append(plan)
        artifacts.sort(key=lambda n: n.created_seq)
        return {"tx": src.tx, "artifacts": [node_dict(n) for n in artifacts]}

    @app.get("/events")
    def events(after: int = 0):
        return [event_dict(e) for e in store.events if e.seq > after]

    @app.get("/strategies")
    def strategies():
        return registry.list()

    @app.get("/media/{name}")
    def get_media(name: str):
        """Serve content-addressed image bytes. Not store-touching, so a
        plain 404 HTTPException is fine here."""
        found = media.read(name)
        if found is None:
            raise HTTPException(404, f"unknown media {name!r}")
        data, content_type = found
        return Response(content=data, media_type=content_type)

    # -- settings (model/provider ergonomics) --------------------------------

    @app.get("/settings")
    def get_settings():
        import importlib.util
        return {**settings.redacted(),
                "litellm_available":
                    importlib.util.find_spec("litellm") is not None}

    @app.put("/settings")
    def put_settings(patch: dict[str, Any]):
        unknown = set(patch) - {"default_model", "api_keys",
                                "strategy_models", "default_params"}
        if unknown:
            raise HTTPException(422, f"unknown settings keys: {sorted(unknown)}")
        return settings.update(patch)

    # -- SSE: live log replication --------------------------------------------

    @app.get("/events/stream")
    async def events_stream(request: Request, after: int = 0,
                            follow: bool = True):
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def listener(committed):
            for ev in committed:
                loop.call_soon_threadsafe(queue.put_nowait, ev)

        store.add_listener(listener)

        async def stream() -> AsyncIterator[str]:
            try:
                for ev in store.events:            # replay first
                    if ev.seq > after:
                        yield sse("event", event_dict(ev))
                if not follow:                     # replay-only mode
                    return
                while True:                        # then follow live
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
                        continue
                    yield sse("event", event_dict(ev))
            finally:
                store.remove_listener(listener)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # -- SSE: generation --------------------------------------------------------

    @app.post("/generate")
    async def generate(req: GenerateReq):
        try:
            strategy = registry.get(req.strategy)
            ctx = StoreBranchContext(store, req.leaf)
        except KeyError as e:
            raise HTTPException(404, str(e))

        # settings may resolve a real model for this request; rebind the
        # strategy for this call only (registry instances stay untouched)
        model_override = settings.resolve_model(req.strategy, req.params)
        if model_override:
            try:
                bound = build_model(model_override, settings)
                strategy = strategy.for_model(bound)
            except RuntimeError as e:
                message = str(e)  # `e` is cleared after the except block

                async def failed() -> AsyncIterator[str]:
                    yield sse("error", {"message": message})
                return StreamingResponse(failed(),
                                         media_type="text/event-stream")

        async def stream() -> AsyncIterator[str]:
            buf: list[str] = []
            try:
                gen_params = {**settings.generation_defaults(),
                              **req.params, "author": req.author}
                async for token in strategy.generate(ctx, gen_params):
                    buf.append(token)
                    yield sse("token", {"delta": token})
                prompt = ctx.prompt if ctx.prompt is not None \
                    else ctx.materialize()
                ctx_hash = hash_messages(prompt)

                if ctx.planned:
                    # strategy declared its nodes: commit all in ONE tx
                    planned_events = [
                        E.Append(payload=E.AppendPayload(
                            node=uuid4().hex, parent=req.leaf,
                            author=p["author"] or req.author,
                            content=p["content"], scope=p["scope"],
                            meta=GenerationMeta(
                                strategy=strategy.id,
                                model=getattr(strategy, "model_name",
                                              "unknown"),
                                params={**req.params, **p["meta_extra"]},
                                watermark=ctx.watermark,
                                context_hash=ctx_hash)))
                        for p in ctx.planned
                    ]
                    committed = store.commit(
                        planned_events, actor=f"strategy:{strategy.id}")
                else:
                    # legacy single-append contract
                    meta = GenerationMeta(
                        strategy=strategy.id,
                        model=getattr(strategy, "model_name", "unknown"),
                        params=req.params,
                        watermark=ctx.watermark,
                        context_hash=ctx_hash)
                    committed = [store.append_node(
                        req.leaf, req.author, "".join(buf), meta=meta,
                        actor=f"strategy:{strategy.id}")]

                story_events = [e for e in committed
                                if e.payload.scope == "story"]
                yield sse("committed", {
                    "events": [event_dict(e) for e in committed],
                    # backward compat: the first story append
                    "event": event_dict(story_events[0])
                             if story_events else None,
                })
            except InvariantError as e:
                yield sse("error", {"message": f"commit failed: {e}"})
            except Exception as e:  # strategy/model failure mid-stream
                yield sse("error", {"message": str(e)})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
