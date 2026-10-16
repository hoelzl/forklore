"""forklore server: FastAPI + SSE over the core event log.

  * REST for commits (append/revise/diverge/tombstone/undo, ...),
    one HTTP request -> one store tx.
  * SSE for token streams during generation, and for log replication to
    the client ("events after seq N", resumable via Last-Event-ID, which
    maps directly onto seq).
  * Strategy registry + BranchContext construction.
  * Thin client: the server owns fold/materialize; the web UI renders
    projections and never reimplements core semantics.

Run a dev server with a deterministic fake model:

    python -m server            # http://127.0.0.1:8000/docs
"""
from .app import create_app
from .context import StoreBranchContext
from .registry import StrategyRegistry

__all__ = ["create_app", "StoreBranchContext", "StrategyRegistry"]
