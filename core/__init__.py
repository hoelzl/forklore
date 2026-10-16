"""forklore core: an event-sourced story tree.

The conversation is a tree of immutable-identity nodes with versioned
content; every change is an event in an append-only log; the visible
state is a pure fold. This package has zero LLM-framework dependencies
on purpose -- it is the scientific instrument, and strategies (which may
use LangGraph & co.) live outside it.
"""
from .events import (
    AnyEvent, Append, Variant, Revise, Graft, Tombstone, Restore,
    SetMeta, Pin, Unpin, GenerationMeta, NodeId,
)
from .state import Node, TreeState, apply, fold
from .store import EventStore, InvariantError
from .materialize import Message, materialize, stale, hash_messages
from .undo import compensations

__all__ = [
    "AnyEvent", "Append", "Variant", "Revise", "Graft", "Tombstone",
    "Restore", "SetMeta", "Pin", "Unpin", "GenerationMeta", "NodeId",
    "Node", "TreeState", "apply", "fold",
    "EventStore", "InvariantError",
    "Message", "materialize", "stale", "hash_messages",
    "compensations",
]
