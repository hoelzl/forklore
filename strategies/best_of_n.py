"""best_of_n: candidate generation + critic scoring -> top-k committed as
story siblings.

This is the first strategy that uses the tree as an orchestration
substrate rather than a history format:

  1. materialize the branch (and record the exact prompt);
  2. draw N candidate continuations from the model;
  3. score each with a critic pass (the same model by default);
  4. plan ALL candidates as workspace nodes (score in meta -- nothing the
     strategy produced is lost, rejected drafts stay inspectable);
  5. plan the top-k as story siblings (rank + score in meta);
  6. stream the winner's text to the client.

The server commits everything in ONE transaction: the whole experiment is
a single undo unit, and GenerationMeta.watermark + context_hash make it
reproducible.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator

from core.materialize import Message

from .models import ChatModel
from .simple_passthrough import truncate

_SCORE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_score(text: str) -> float:
    """First number in the critic's reply, clamped to [1, 10]; a reply
    with no number at all scores 5 (neutral) rather than failing."""
    m = _SCORE_RE.search(text)
    if not m:
        return 5.0
    return max(1.0, min(10.0, float(m.group(1))))


def critic_prompt(base: list[Message], candidate: str) -> Message:
    tail = "\n\n".join(m.content for m in base[-4:])
    return Message(
        node="", author="critic", scope="story", version=1, pinned=False,
        content=(
            "Rate how strong this continuation is for the story so far — "
            "coherence, voice, and forward momentum. Reply with a single "
            "number from 1 to 10.\n\n"
            f"Story so far:\n{tail}\n\n"
            f"Candidate continuation:\n{candidate}\n\n"
            "Rating:"))


async def collect(stream: AsyncIterator[str]) -> str:
    return "".join([t async for t in stream])


class BestOfN:
    id = "best_of_n/0.1"

    def __init__(self, model: ChatModel, critic: ChatModel | None = None):
        self.model = model
        self.critic = critic or model
        self.model_name = model.name

    def for_model(self, model: ChatModel) -> "BestOfN":
        """A copy bound to a different model; the critic follows the new
        model unless one was explicitly supplied at construction."""
        explicit = None if self.critic is self.model else self.critic
        return BestOfN(model, critic=explicit)

    async def generate(self, ctx, params: dict[str, Any]) -> AsyncIterator[str]:
        n = int(params.get("n", 3))
        k = max(1, min(int(params.get("k", 2)), n))
        author = params.get("author", "character")

        base = truncate(ctx.materialize(), params.get("max_messages"))
        ctx.record_prompt(base)

        # 1-2. candidates
        seed = int(params.get("seed", 0))
        candidates = []
        for i in range(n):
            text = await collect(self.model.stream(
                base, {**params, "seed": seed + i}))
            if text.strip():
                candidates.append(text)

        # 3. critic scoring
        scored = []
        for text in candidates:
            reply = await collect(self.critic.stream(
                [critic_prompt(base, text)], {}))
            scored.append((parse_score(reply), text))
        ranked = sorted(scored, key=lambda x: -x[0])

        # 4. every candidate -> workspace (inspectable, rejected included)
        for score, text in ranked:
            ctx.plan_node(content=text, scope="workspace",
                          meta_extra={"score": score, "pool": n})

        # 5. top-k -> story siblings
        winners = ranked[:k]
        for rank, (score, text) in enumerate(winners):
            ctx.plan_node(content=text, scope="story", author=author,
                          meta_extra={"score": score, "rank": rank,
                                      "n": n, "k": k})

        # 6. stream the winner (word-by-word, like a live generation)
        words = winners[0][1].split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "
