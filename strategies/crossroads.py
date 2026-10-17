"""crossroads: choice-driven play strategy.

Each turn: the materialized branch (+ optional steering briefs) + one
`guide` meta-role message carrying the reader's choice and the author's
one-shot hidden guidance -> one model call in a PASSAGE:/CHOICES: format
-> ONE story node whose GenerationMeta.params carry the choices offered
next turn and the choice/guidance that produced this node. A generated
image (content-addressed in the media store) is committed as node meta
in the same transaction.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator

from core.materialize import Message

from .best_of_n import collect
from .images import DemoImageModel, ImageModel
from .models import ChatModel
from .simple_passthrough import truncate

_CHOICE_LINE_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s*")


def parse_crossroads_output(text: str) -> tuple[str, list[str]]:
    """Split a model reply into (passage, choices).

    Degrades gracefully: no PASSAGE: marker -> the whole reply is the
    passage; no CHOICES: marker -> no choices. Choice lines may be
    bulleted or numbered; at most three are kept.
    """
    body = text.strip()
    choices: list[str] = []
    if "CHOICES:" in body:
        body, _, choice_block = body.partition("CHOICES:")
        for line in choice_block.strip().splitlines():
            choice = _CHOICE_LINE_RE.sub("", line.strip()).strip()
            if choice:
                choices.append(choice)
        choices = choices[:3]
    if "PASSAGE:" in body:
        body = body.split("PASSAGE:", 1)[1]
    return body.strip(), choices


def _msg(author: str, content: str) -> Message:
    """An unpersisted prompt message (mirrors beat_planner._msg)."""
    return Message(node="", author=author, content=content,
                   version=1, pinned=False, scope="story")


class Crossroads:
    id = "crossroads/0.1"

    def __init__(self, model: ChatModel,
                 image_model: ImageModel | None = None):
        self.model = model
        self.model_name = model.name
        self.image_model = image_model or DemoImageModel()

    def for_model(self, model: ChatModel) -> "Crossroads":
        return Crossroads(model, self.image_model)

    def for_image_model(self, image_model: ImageModel) -> "Crossroads":
        return Crossroads(self.model, image_model)

    async def generate(self, ctx, params: dict[str, Any]) -> AsyncIterator[str]:
        author = params.get("author", "character")
        choice = (params.get("choice") or "").strip()
        guidance = (params.get("guidance") or "").strip()

        prompt = list(truncate(ctx.materialize(), params.get("max_messages")))
        for m in ctx.steering():
            prompt.append(_msg("director",
                               f"Story brief (steering):\n{m.content}"))
        prompt.append(_msg("guide",
            (f"The reader chose: {choice}." if choice else
             "The reader lets you choose the direction.")
            + (f"\nDirection from the author (not part of the story): "
               f"{guidance}" if guidance else "")
            + "\n\nWrite the next passage, then propose three short "
              "choices for what happens next. Reply in exactly this "
              "format:\n\nPASSAGE:\n<the next passage>\n\nCHOICES:\n"
              "- <first choice>\n- <second choice>\n- <third choice>"))
        ctx.record_prompt(prompt)

        raw = await collect(self.model.stream(prompt, {}))
        passage, choices = parse_crossroads_output(raw)
        if not passage:
            # empty passage: fall back to the pre-CHOICES body, never to
            # the raw reply (which would carry the markers into the story)
            passage = raw.partition("CHOICES:")[0].removeprefix("PASSAGE:").strip()

        # excerpt first, so the demo plate's title word is passage-specific
        image_prompt = (" ".join(passage.split()[:40])
                        + " — illustration for a story passage")
        image = None
        try:
            image = await self.image_model.generate(image_prompt, params)
        except Exception:
            image = None  # the story is primary; the picture is decoration

        ctx.plan_node(
            content=passage, scope="story", author=author,
            meta_extra={"choices": choices,
                        "choice": choice or None,
                        "guidance": guidance or None,
                        "image_prompt": image_prompt},
            image=image)

        words = passage.split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "
