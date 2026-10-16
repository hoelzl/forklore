"""Materialization: branch -> ordered message list.

THE hot path. Everything generation-related consumes its output, and it is
a pure function of (state, leaf) -- and since `state` is itself a pure
fold of a log prefix, `materialize(state(at=N), leaf)` reconstructs
exactly what any reader saw at seq N. That is the property GenerationMeta's
watermark + context_hash bank on.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .events import NodeId
from .state import TreeState


@dataclass
class Message:
    node: NodeId
    author: str
    content: str
    version: int          # which content version was resolved
    pinned: bool          # True if a pin (not "latest") selected the version
    scope: str            # "story" | "workspace"


def resolve_version(state: TreeState, node_id: NodeId,
                    via_child: NodeId | None) -> tuple[int, bool]:
    """Pin first, else latest. Pins are only consulted for the edge
    actually on the path, so dangling pins (edge destroyed by a later
    Graft) are automatically inert."""
    node = state.nodes[node_id]
    if via_child is not None:
        pinned = state.pins.get((node_id, via_child))
        if pinned is not None and pinned <= node.version:
            return pinned, True
    return node.version, False


def materialize(state: TreeState, leaf: NodeId,
                *, include_workspace: bool = False) -> list[Message]:
    """Walk leaf -> root, resolve each node's content, return root -> leaf.

    Workspace nodes never appear unless explicitly requested (strategies
    reading their own scratch use include_workspace=True).
    Tombstones never affect materialization -- only discoverability.
    """
    path = state.path_to_root(leaf)          # leaf .. root
    out: list[Message] = []
    for i, node_id in enumerate(reversed(path)):
        # path[len-1-i] is node_id; the next node toward the leaf is
        # path[len-2-i] (None when node_id is the leaf itself)
        via_child = path[len(path) - 2 - i] if i < len(path) - 1 else None
        version, pinned = resolve_version(state, node_id, via_child)
        node = state.nodes[node_id]
        out.append(Message(
            node=node_id, author=node.author,
            content=node.versions[version - 1],
            version=version, pinned=pinned, scope=node.scope))
    if not include_workspace:
        out = [m for m in out if m.scope == "story"]
    return out


def stale(state: TreeState, node_id: NodeId) -> list[NodeId]:
    """Ancestors whose visible text changed after this node was written:
    "this continuation was generated against an earlier version upstream."

    A pinned edge is deliberately frozen, not stale.
    """
    node = state.nodes[node_id]
    out = []
    path = state.path_to_root(node_id)       # node .. root
    for ancestor in path[1:]:
        a = state.nodes[ancestor]
        if a.last_revise_seq <= node.created_seq:
            continue
        via = state.child_on_path(ancestor, node_id)
        if via is not None and (ancestor, via) in state.pins:
            continue
        out.append(ancestor)
    return out


def hash_messages(messages: list[Message]) -> str:
    """Hash of the exact content a model would see. Strategies should hash
    the *post-truncation* prompt messages and record it as
    GenerationMeta.context_hash."""
    raw = json.dumps([(m.author, m.content) for m in messages],
                     ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()
