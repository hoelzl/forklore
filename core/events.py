"""Event schema for the forklore append-only log.

Every state change in forklore is an event. The visible story tree is a
materialized view: a fold over events 1..N in seq order. Nothing is ever
removed from the log -- "undo" is a compensating event (see core.undo),
"deletion" is a Tombstone. This makes the whole history -- including the
history of mistakes -- preserved, replayable, and time-travelable:

    state(at=N) == fold(log[0..N])   # pure function, forever
"""
from __future__ import annotations

import time
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter

NodeId = str
AuthorId = str


class GenerationMeta(BaseModel):
    """Reproducibility record attached to machine-generated nodes.

    watermark:     log seq the generation context was materialized at,
                   i.e. "this reply was written against tree-as-of-watermark".
    context_hash:  hash of the exact message list sent to the model,
                   post-truncation (see core.materialize.hash_messages).
    workspace:     scratch nodes (scope="workspace") produced in the same
                   transaction by an orchestrating strategy.
    """

    strategy: str            # registry id + version of the strategy
    model: str
    params: dict[str, Any] = {}
    seed: int | None = None
    watermark: int = 0
    context_hash: str = ""
    workspace: list[NodeId] = []


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

class AppendPayload(BaseModel):
    node: NodeId
    parent: NodeId | None                 # None only for the root
    author: AuthorId
    content: str                          # version 1 of this node
    scope: Literal["story", "workspace"] = "story"
    meta: GenerationMeta | None = None    # present iff machine-generated


class RevisePayload(BaseModel):
    """New content version of the *same* node identity.

    Propagates to every branch passing through the node. The typo-fix
    primitive. Never destroys the old version.
    """

    node: NodeId
    content: str


class GraftPayload(BaseModel):
    """Re-parent a subtree. Moves exactly one pointer."""

    subtree: NodeId
    new_parent: NodeId


class NodePayload(BaseModel):
    node: NodeId


class SetMetaPayload(BaseModel):
    node: NodeId
    key: str
    value: Any = None


class PinPayload(BaseModel):
    """Edge-anchored version pin (v1.1 feature, schema reserved now).

    "Materializations whose path passes from `node` through `via_child`
    see `node` at `version`." A pin whose edge is later destroyed by a
    Graft becomes dangling: retained in the log, semantically inert.
    """

    node: NodeId
    via_child: NodeId
    version: int


class UnpinPayload(BaseModel):
    node: NodeId
    via_child: NodeId


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

class EventBase(BaseModel):
    seq: int = 0                          # assigned by the store on commit
    id: str = Field(default_factory=lambda: uuid4().hex)
    tx: str = ""                          # transaction id = grouped undo unit
    ts: float = Field(default_factory=time.time)  # display only; seq orders
    actor: str = "user"                   # user id, or "strategy:<id>"
    undoes: str | None = None             # tx id, set on compensating events


class Append(EventBase):
    kind: Literal["append"] = "append"
    payload: AppendPayload


class Variant(EventBase):
    """Same shape as Append; the kind label records intent (divergence,
    not fresh continuation) for UX and analytics."""

    kind: Literal["variant"] = "variant"
    payload: AppendPayload


class Revise(EventBase):
    kind: Literal["revise"] = "revise"
    payload: RevisePayload


class Graft(EventBase):
    kind: Literal["graft"] = "graft"
    payload: GraftPayload


class Tombstone(EventBase):
    """Hides a subtree from default views. The only kind of deletion."""

    kind: Literal["tombstone"] = "tombstone"
    payload: NodePayload


class Restore(EventBase):
    kind: Literal["restore"] = "restore"
    payload: NodePayload


class SetMeta(EventBase):
    kind: Literal["set_meta"] = "set_meta"
    payload: SetMetaPayload


class Pin(EventBase):
    kind: Literal["pin"] = "pin"
    payload: PinPayload


class Unpin(EventBase):
    kind: Literal["unpin"] = "unpin"
    payload: UnpinPayload


AnyEvent = Annotated[
    Append | Variant | Revise | Graft | Tombstone | Restore | SetMeta | Pin | Unpin,
    Field(discriminator="kind"),
]

event_adapter: TypeAdapter = TypeAdapter(AnyEvent)


def dumps(event: AnyEvent) -> str:
    return event.model_dump_json()


def loads(raw: str) -> AnyEvent:
    return event_adapter.validate_json(raw)
