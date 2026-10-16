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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import EventStore, GenerationMeta, InvariantError
from core import events as E
from core.materialize import hash_messages
from core.state import Node, TreeState

from .context import StoreBranchContext
from .registry import StrategyRegistry


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
        "last_revise_seq": n.last_revise_seq,
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


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

def create_app(store: EventStore, registry: StrategyRegistry) -> FastAPI:
    app = FastAPI(title="forklore")

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

    @app.get("/events")
    def events(after: int = 0):
        return [event_dict(e) for e in store.events if e.seq > after]

    @app.get("/strategies")
    def strategies():
        return registry.list()

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

        async def stream() -> AsyncIterator[str]:
            buf: list[str] = []
            try:
                gen_params = {**req.params, "author": req.author}
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
