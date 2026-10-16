"""TreeState: the materialized projection of the event log.

A Node has a stable *identity* (its slot in the tree; children point to the
identity, never to content) and a versioned *content*. All semantics are
defined by `fold`: applying events in seq order always yields the same
state, so rebuilds after a crash or projections at a watermark are just
replays.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .events import (
    AnyEvent, Append, Variant, Revise, Graft,
    Tombstone, Restore, SetMeta, Pin, Unpin,
    GenerationMeta, NodeId,
)


@dataclass
class Node:
    id: NodeId
    parent: NodeId | None
    author: str
    scope: str                          # "story" | "workspace"
    created_seq: int
    versions: list[str]                 # versions[0] == v1
    generation: GenerationMeta | None = None
    tombstoned: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    children: list[NodeId] = field(default_factory=list)
    last_revise_seq: int = 0            # seq of latest Revise (or creation)

    @property
    def content(self) -> str:
        return self.versions[-1]

    @property
    def version(self) -> int:
        return len(self.versions)


class TreeState:
    def __init__(self) -> None:
        self.nodes: dict[NodeId, Node] = {}
        self.pins: dict[tuple[NodeId, NodeId], int] = {}  # (node, via_child) -> version
        self.root: NodeId | None = None
        self.head: int = 0              # highest seq folded

    # -- queries ----------------------------------------------------------

    def path_to_root(self, node_id: NodeId) -> list[NodeId]:
        """node .. root (in that order)."""
        if node_id not in self.nodes:
            raise KeyError(
                f"node {node_id!r} does not exist in this projection "
                f"(was it created after the watermark?)")
        path = []
        cur: NodeId | None = node_id
        while cur is not None:
            path.append(cur)
            cur = self.nodes[cur].parent
        return path

    def descendants(self, node_id: NodeId) -> set[NodeId]:
        out: set[NodeId] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for c in self.nodes[cur].children:
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    def child_on_path(self, ancestor: NodeId, descendant: NodeId) -> NodeId | None:
        """The child of `ancestor` through which `descendant` is reached."""
        prev = descendant
        cur: NodeId | None = descendant
        while cur is not None and cur != ancestor:
            prev = cur
            cur = self.nodes[cur].parent
        return prev if cur == ancestor else None

    def is_hidden(self, node_id: NodeId) -> bool:
        """Tombstoned, or has a tombstoned ancestor.

        Hidden affects *discoverability* (tree view, cursor navigation),
        never materialization.
        """
        return any(self.nodes[n].tombstoned for n in self.path_to_root(node_id))

    def visible_children(self, node_id: NodeId) -> list[NodeId]:
        return [c for c in self.nodes[node_id].children
                if not self.nodes[c].tombstoned]


# ---------------------------------------------------------------------------
# fold
# ---------------------------------------------------------------------------

def apply(state: TreeState, ev: AnyEvent) -> None:
    """Pure application of one event. Validation lives in core.store;
    the fold assumes an already-valid log and is total."""
    p = ev.payload
    if isinstance(ev, (Append, Variant)):
        node = Node(
            id=p.node, parent=p.parent, author=p.author, scope=p.scope,
            created_seq=ev.seq, versions=[p.content], generation=p.meta,
            last_revise_seq=ev.seq,
        )
        state.nodes[p.node] = node
        if p.parent is None:
            state.root = p.node
        else:
            state.nodes[p.parent].children.append(p.node)
    elif isinstance(ev, Revise):
        node = state.nodes[p.node]
        node.versions.append(p.content)
        node.last_revise_seq = ev.seq
    elif isinstance(ev, Graft):
        node = state.nodes[p.subtree]
        if node.parent is not None:
            state.nodes[node.parent].children.remove(p.subtree)
        node.parent = p.new_parent
        state.nodes[p.new_parent].children.append(p.subtree)
    elif isinstance(ev, Tombstone):
        state.nodes[p.node].tombstoned = True
    elif isinstance(ev, Restore):
        state.nodes[p.node].tombstoned = False
    elif isinstance(ev, SetMeta):
        state.nodes[p.node].meta[p.key] = p.value
    elif isinstance(ev, Pin):
        state.pins[(p.node, p.via_child)] = p.version
    elif isinstance(ev, Unpin):
        state.pins.pop((p.node, p.via_child), None)
    state.head = max(state.head, ev.seq)


def fold(events: Iterable[AnyEvent]) -> TreeState:
    state = TreeState()
    for ev in events:
        apply(state, ev)
    return state
