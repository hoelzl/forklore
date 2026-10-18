"""forklore strategies: pluggable reply-generation architectures.

A strategy is anything implementing GenerationStrategy. It receives a
BranchContext -- read access to the tree (materialize the branch, inspect
the whole tree at a watermark, read workspace scratch nodes) -- and writes
results back *only* by emitting events through the store (the server
commits on its behalf). It never mutates state directly.

The spectrum, all behind the same interface:

    simple_passthrough   materialized history -> one model call
    best_of_n            N candidates -> scoring -> top-k committed as siblings
    langgraph_crew       director/drafter/critic orchestration; scratch work
                         persisted as scope="workspace" nodes in one tx
    crossroads           choice-driven play: `guide` meta-role carries the
                         reader's choice; per-node image committed as meta
                         in the same tx

LangGraph & co. are per-strategy implementation details and must never
leak into core. The registry key (GenerationMeta.strategy) is recorded on
every generated node so experiments can compare strategies that generated
siblings of the same parent.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from core.events import NodeId
from core.materialize import Message
from core.state import TreeState


class BranchContext(Protocol):
    """What a strategy is allowed to see. Constructed by the server per
    generation request; the contract is part of the experimentation
    surface, so keep it small and explicit."""

    leaf: NodeId
    watermark: int          # store.head captured when the context was built

    def materialize(self, *, at: int | None = None,
                    include_workspace: bool = False) -> list[Message]: ...
    def tree_at(self, at: int | None = None) -> TreeState: ...
    def workspace(self) -> list[NodeId]:
        """Existing workspace children of the leaf (prior scratch)."""
        ...

    def record_prompt(self, messages: list[Message]) -> None:
        """Strategies MUST call this with the exact (post-truncation)
        message list sent to the model. The server hashes it into
        GenerationMeta.context_hash."""
        ...

    def plan_node(self, *, content: str, scope: str = "story",
                  author: str | None = None,
                  meta_extra: dict[str, Any] | None = None,
                  meta: dict[str, Any] | None = None,
                  image: bytes | None = None) -> None:
        """Declare a node to be committed under the leaf when generation
        completes. All planned nodes are committed by the server in ONE
        transaction (one undo unit), each stamped with GenerationMeta
        (strategy, model, watermark, context_hash; meta_extra merged into
        params). `meta` becomes node meta via SetMeta events in the same
        tx; `image` is saved to the media store and referenced as meta
        "image" in the same tx. The meta key "image" is reserved for the
        media-store reference (set from the image argument).
        scope="workspace" keeps scratch/rejected candidates out of the
        story view but in the log.

        A strategy that never calls plan_node gets the legacy behavior:
        the streamed text is committed as a single story Append."""
        ...

    def steering(self) -> list[Message]:
        """Steering briefs (kind="steering" meta) on the branch path,
        root -> leaf, pin-resolved. Empty when the branch has none."""
        ...


class GenerationStrategy(Protocol):
    id: str                 # registry id, e.g. "simple_passthrough/0.1"
    model_name: str         # recorded in GenerationMeta.model

    def generate(self, ctx: BranchContext,
                 params: dict[str, Any]) -> AsyncIterator[str]:
        """Yield token deltas; the server commits the final Append (with
        GenerationMeta watermark + context_hash) on completion."""
        ...
