"""simple_passthrough: the trivial end of the strategy spectrum.

Materialized branch -> (optional naive truncation) -> one model call ->
stream tokens. This is the baseline every fancier strategy is compared
against, and it exercises the full contract: read via BranchContext,
record the exact prompt, stream, let the server commit.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from core.materialize import Message

from .models import ChatModel


def truncate(messages: list[Message], max_messages: int | None) -> list[Message]:
    """Naive budget policy: keep the root (scenario setup) plus the most
    recent messages. Real strategies replace this with token accounting
    and summarization -- it lives here, not in core, because the budget
    policy is part of the strategy, and the recorded context_hash covers
    whatever actually survives it."""
    if max_messages is None or len(messages) <= max_messages:
        return messages
    return [messages[0], *messages[-(max_messages - 1):]]


class SimplePassthrough:
    id = "simple_passthrough/0.1"

    def __init__(self, model: ChatModel):
        self.model = model
        self.model_name = model.name

    async def generate(self, ctx, params: dict[str, Any]) -> AsyncIterator[str]:
        prompt = truncate(ctx.materialize(), params.get("max_messages"))
        ctx.record_prompt(prompt)
        async for token in self.model.stream(prompt, params):
            yield token
