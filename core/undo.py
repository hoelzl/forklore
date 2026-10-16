"""Undo as compensating events.

Undo never rewrites the log: it *appends* ordinary events that invert the
original transaction's effect, computed against the pre-transaction state.
Consequences:

  * the fold never special-cases undo;
  * the full history of mistakes is preserved and itself undoable --
    redo is just `undo(undo_tx)`;
  * compensation is computed against current head state where it matters
    (e.g. an Append compensation is skipped if the node was already
    tombstoned since), so interleaved later work is not clobbered.

Compensations are ordinary events and pass through normal invariant
checking, so an undo that no longer makes structural sense (rare in
single-user use) fails loudly with InvariantError rather than corrupting
the tree.

    original (in tx)      compensating event(s)
    -------------------   ----------------------------------
    Append / Variant      Tombstone(node)
    Revise(node, c)       Revise(node, content-before-tx)
    Graft(s, p)           Graft(s, parent-before-tx)
    Tombstone / Restore   Restore / Tombstone
    Pin / Unpin           Unpin / Pin(version-before-tx)
    SetMeta(k, v)         SetMeta(k, value-before-tx)
"""
from __future__ import annotations

from . import events as E
from .events import AnyEvent
from .state import fold


def compensations(log: list[AnyEvent], tx: str) -> list[AnyEvent]:
    group = [e for e in log if e.tx == tx]
    if not group:
        raise ValueError(f"no events with tx={tx!r}")
    pre = fold(log[: group[0].seq - 1])   # state just before the tx
    head = fold(log)

    out: list[AnyEvent] = []
    for ev in reversed(group):
        p = ev.payload
        if isinstance(ev, (E.Append, E.Variant)):
            if not head.nodes[p.node].tombstoned:
                out.append(E.Tombstone(payload=E.NodePayload(node=p.node)))
        elif isinstance(ev, E.Revise):
            before = pre.nodes[p.node].content
            if head.nodes[p.node].content != before:
                out.append(E.Revise(payload=E.RevisePayload(
                    node=p.node, content=before)))
        elif isinstance(ev, E.Graft):
            before_parent = pre.nodes[p.subtree].parent
            if head.nodes[p.subtree].parent != before_parent \
                    and before_parent is not None:
                out.append(E.Graft(payload=E.GraftPayload(
                    subtree=p.subtree, new_parent=before_parent)))
        elif isinstance(ev, E.Tombstone):
            if head.nodes[p.node].tombstoned:
                out.append(E.Restore(payload=E.NodePayload(node=p.node)))
        elif isinstance(ev, E.Restore):
            if not head.nodes[p.node].tombstoned:
                out.append(E.Tombstone(payload=E.NodePayload(node=p.node)))
        elif isinstance(ev, E.Pin):
            key = (p.node, p.via_child)
            if key in pre.pins:
                out.append(E.Pin(payload=E.PinPayload(
                    node=p.node, via_child=p.via_child,
                    version=pre.pins[key])))
            elif key in head.pins:
                out.append(E.Unpin(payload=E.UnpinPayload(
                    node=p.node, via_child=p.via_child)))
        elif isinstance(ev, E.Unpin):
            key = (p.node, p.via_child)
            if key in pre.pins and key not in head.pins:
                out.append(E.Pin(payload=E.PinPayload(
                    node=p.node, via_child=p.via_child,
                    version=pre.pins[key])))
        elif isinstance(ev, E.SetMeta):
            before = pre.nodes[p.node].meta.get(p.key)
            if head.nodes[p.node].meta.get(p.key) != before:
                out.append(E.SetMeta(payload=E.SetMetaPayload(
                    node=p.node, key=p.key, value=before)))
    return out
