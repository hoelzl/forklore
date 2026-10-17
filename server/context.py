"""StoreBranchContext: the BranchContext implementation handed to
strategies during generation. Read-only with respect to the log; the
watermark is captured at construction so GenerationMeta can say exactly
which tree the strategy saw."""
from __future__ import annotations

from core.events import NodeId
from core.materialize import Message
from core.state import TreeState
from core.store import EventStore


class StoreBranchContext:
    def __init__(self, store: EventStore, leaf: NodeId):
        if leaf not in store.state().nodes:
            raise KeyError(f"unknown leaf {leaf!r}")
        self._store = store
        self.leaf = leaf
        self.watermark = store.head
        self.prompt: list[Message] | None = None
        self.planned: list[dict] = []

    def materialize(self, *, at: int | None = None,
                    include_workspace: bool = False) -> list[Message]:
        return self._store.materialize(
            self.leaf, at=at, include_workspace=include_workspace)

    def tree_at(self, at: int | None = None) -> TreeState:
        """Whole-tree read access: strategies may look sideways at other
        branches (e.g. best-of-N scoring against abandoned alternatives)."""
        return self._store.state(at)

    def workspace(self) -> list[NodeId]:
        state = self._store.state()
        return [c for c in state.nodes[self.leaf].children
                if state.nodes[c].scope == "workspace"]

    def record_prompt(self, messages: list[Message]) -> None:
        self.prompt = list(messages)

    def plan_node(self, *, content: str, scope: str = "story",
                  author: str | None = None,
                  meta_extra: dict | None = None,
                  meta: dict | None = None,
                  image: bytes | None = None) -> None:
        """Declare a node to be committed under the leaf on completion
        (the server commits all planned nodes in one transaction).
        `meta` becomes node meta via SetMeta events in the same tx;
        `image` is saved to the media store and referenced as meta
        "image" in the same tx. Note: the meta key "image" is reserved for the media-store reference (set from the image argument)."""
        self.planned.append({
            "content": content, "scope": scope,
            "author": author, "meta_extra": meta_extra or {},
            "meta": meta or {}, "image": image,
        })

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
