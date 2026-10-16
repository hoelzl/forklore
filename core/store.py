"""EventStore: the append-only log (SQLite) + invariant checking.

The log is the arbiter: an event that violates an invariant is rejected
and never written. Seq numbers are assigned at commit time (gapless,
single writer), and one commit = one transaction = one undo unit.
"""
from __future__ import annotations

import copy
import sqlite3
import threading
from uuid import uuid4

from . import events as E
from .events import AnyEvent, NodeId, dumps, loads
from .state import TreeState, apply, fold


class InvariantError(Exception):
    """Raised when an event violates a structural invariant."""


# ---------------------------------------------------------------------------
# Invariants (checked against a trial state before anything is written)
# ---------------------------------------------------------------------------

def validate(state: TreeState, ev: AnyEvent) -> None:
    p = ev.payload
    if isinstance(ev, (E.Append, E.Variant)):
        if p.node in state.nodes:
            raise InvariantError(f"node {p.node!r} already exists (ids are never reused)")
        if p.parent is None:
            if state.root is not None:
                raise InvariantError("a root already exists")
        else:
            if p.parent not in state.nodes:
                raise InvariantError(f"unknown parent {p.parent!r}")
            if state.nodes[p.parent].tombstoned:
                raise InvariantError(
                    f"parent {p.parent!r} is tombstoned; Restore it first")
    elif isinstance(ev, E.Revise):
        if p.node not in state.nodes:
            raise InvariantError(f"unknown node {p.node!r}")
        # Revising a tombstoned node is allowed: fixing text in a deleted
        # branch is harmless and keeps the rule set minimal.
    elif isinstance(ev, E.Graft):
        if p.subtree not in state.nodes or p.new_parent not in state.nodes:
            raise InvariantError("graft of unknown node")
        if p.subtree == state.root:
            raise InvariantError("cannot graft the root")
        if p.new_parent == p.subtree or p.new_parent in state.descendants(p.subtree):
            raise InvariantError("graft would create a cycle")
        if state.nodes[p.new_parent].tombstoned:
            raise InvariantError("cannot graft under a tombstoned node")
    elif isinstance(ev, (E.Tombstone, E.Restore)):
        if p.node not in state.nodes:
            raise InvariantError(f"unknown node {p.node!r}")
        # Both are idempotent: redundant events are written (auditable)
        # but have no effect.
    elif isinstance(ev, E.SetMeta):
        if p.node not in state.nodes:
            raise InvariantError(f"unknown node {p.node!r}")
    elif isinstance(ev, E.Pin):
        if p.node not in state.nodes or p.via_child not in state.nodes:
            raise InvariantError("pin of unknown node")
        if state.nodes[p.via_child].parent != p.node:
            raise InvariantError(
                f"pin requires an edge: {p.via_child!r} is not a child of {p.node!r}")
        if not 1 <= p.version <= state.nodes[p.node].version:
            raise InvariantError(f"pin version {p.version} out of range")
    elif isinstance(ev, E.Unpin):
        if (p.node, p.via_child) not in state.pins:
            raise InvariantError("no such pin")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class EventStore:
    def __init__(self, path: str | None = None):
        # check_same_thread=False: the server handles requests on worker
        # threads. Single-writer discipline is enforced by the commit lock.
        self._conn = sqlite3.connect(path or ":memory:",
                                     check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events("
            " seq INTEGER PRIMARY KEY,"
            " data TEXT NOT NULL)")
        self._conn.commit()
        self._events: list[AnyEvent] = [
            loads(row[0]) for row in
            self._conn.execute("SELECT data FROM events ORDER BY seq")]
        self._state: TreeState = fold(self._events)
        # listeners are notified with committed events after each commit;
        # used by the server for live log replication over SSE
        self._listeners: list = []

    def add_listener(self, callback) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    # -- reads ------------------------------------------------------------

    @property
    def events(self) -> list[AnyEvent]:
        return list(self._events)

    @property
    def head(self) -> int:
        return self._state.head

    def state(self, at: int | None = None) -> TreeState:
        """The projection. `at=None` -> head; else tree-as-of seq `at`
        (time travel is a read-only fold of a prefix)."""
        if at is None or at >= self._state.head:
            return self._state
        return fold(self._events[:at])

    # -- writes -----------------------------------------------------------

    def commit(self, events: list[AnyEvent], *, tx: str | None = None,
               actor: str = "user", undoes: str | None = None) -> list[AnyEvent]:
        """Validate all events against a trial state, then write them as
        one SQLite transaction with one shared tx id (one undo unit)."""
        if not events:
            return []
        with self._lock:
            tx = tx or uuid4().hex
            trial = copy.deepcopy(self._state)
            for ev in events:
                validate(trial, ev)
                apply(trial, ev)

            committed = []
            base = self._state.head  # capture once: apply() mutates head
            try:
                with self._conn:
                    for i, ev in enumerate(events, start=1):
                        ev = ev.model_copy(update={
                            "seq": base + i, "tx": tx,
                            "actor": actor, "undoes": undoes})
                        self._conn.execute(
                            "INSERT INTO events(seq, data) VALUES(?, ?)",
                            (ev.seq, dumps(ev)))
                        self._events.append(ev)
                        apply(self._state, ev)
                        committed.append(ev)
                for cb in self._listeners:
                    try:
                        cb(committed)
                    except Exception:
                        pass  # listeners must never break a commit
            except Exception:
                # sqlite transaction rolled back; rebuild in-memory state
                self._events = [loads(r[0]) for r in self._conn.execute(
                    "SELECT data FROM events ORDER BY seq")]
                self._state = fold(self._events)
                raise
            return committed

    # -- convenience gestures (each is one commit; diverge is one tx) -----

    def append_node(self, parent: NodeId | None, author: str, content: str,
                    *, node_id: NodeId | None = None,
                    scope: str = "story", meta: E.GenerationMeta | None = None,
                    tx: str | None = None, actor: str = "user") -> E.Append:
        ev = E.Append(payload=E.AppendPayload(
            node=node_id or uuid4().hex, parent=parent, author=author,
            content=content, scope=scope, meta=meta))  # type: ignore[arg-type]
        return self.commit([ev], tx=tx, actor=actor)[0]  # type: ignore[return-value]

    def revise(self, node: NodeId, content: str, **kw) -> E.Revise:
        ev = E.Revise(payload=E.RevisePayload(node=node, content=content))
        return self.commit([ev], **kw)[0]  # type: ignore[return-value]

    def variant(self, node: NodeId, content: str, *,
                node_id: NodeId | None = None, **kw) -> E.Variant:
        """A sibling of `node` with edited content (the divergence half of
        'edit only this storyline'; pair with Graft via diverge())."""
        parent = self._state.nodes[node].parent
        author = self._state.nodes[node].author
        ev = E.Variant(payload=E.AppendPayload(
            node=node_id or uuid4().hex, parent=parent, author=author,
            content=content, scope=self._state.nodes[node].scope))
        return self.commit([ev], **kw)[0]  # type: ignore[return-value]

    def graft(self, subtree: NodeId, new_parent: NodeId, **kw) -> E.Graft:
        ev = E.Graft(payload=E.GraftPayload(subtree=subtree, new_parent=new_parent))
        return self.commit([ev], **kw)[0]  # type: ignore[return-value]

    def diverge(self, node: NodeId, content: str, move: NodeId,
                *, variant_id: NodeId | None = None,
                actor: str = "user") -> tuple[E.Variant, E.Graft]:
        """'Edit only this storyline' as one undoable gesture:
        Variant(node, content) + Graft(move -> variant) in a single tx."""
        parent = self._state.nodes[node].parent
        ev_v = E.Variant(payload=E.AppendPayload(
            node=variant_id or uuid4().hex, parent=parent,
            author=self._state.nodes[node].author, content=content,
            scope=self._state.nodes[node].scope))
        ev_g = E.Graft(payload=E.GraftPayload(
            subtree=move, new_parent=ev_v.payload.node))
        v, g = self.commit([ev_v, ev_g], actor=actor)
        return v, g  # type: ignore[return-value]

    def tombstone(self, node: NodeId, **kw) -> E.Tombstone:
        return self.commit([E.Tombstone(payload=E.NodePayload(node=node))],
                           **kw)[0]  # type: ignore[return-value]

    def restore(self, node: NodeId, **kw) -> E.Restore:
        return self.commit([E.Restore(payload=E.NodePayload(node=node))],
                           **kw)[0]  # type: ignore[return-value]

    def pin(self, node: NodeId, via_child: NodeId, version: int, **kw) -> E.Pin:
        ev = E.Pin(payload=E.PinPayload(node=node, via_child=via_child,
                                        version=version))
        return self.commit([ev], **kw)[0]  # type: ignore[return-value]

    def unpin(self, node: NodeId, via_child: NodeId, **kw) -> E.Unpin:
        ev = E.Unpin(payload=E.UnpinPayload(node=node, via_child=via_child))
        return self.commit([ev], **kw)[0]  # type: ignore[return-value]

    def set_meta(self, node: NodeId, key: str, value, **kw) -> E.SetMeta:
        ev = E.SetMeta(payload=E.SetMetaPayload(node=node, key=key, value=value))
        return self.commit([ev], **kw)[0]  # type: ignore[return-value]

    # -- undo ---------------------------------------------------------------

    def undo(self, tx: str, *, actor: str = "user") -> list[AnyEvent]:
        """Append compensating events for every event of `tx` (reverse
        order), as one new tx. Undo is itself log data -- undoing the undo
        tx is redo."""
        from .undo import compensations
        comp = compensations(self._events, tx)
        return self.commit(comp, actor=actor, undoes=tx)

    # -- reads, continued ---------------------------------------------------

    def materialize(self, leaf: NodeId, *, at: int | None = None,
                    include_workspace: bool = False):
        from .materialize import materialize
        return materialize(self.state(at), leaf,
                           include_workspace=include_workspace)

    def stale(self, node: NodeId) -> list[NodeId]:
        from .materialize import stale
        return stale(self._state, node)
