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

_CHOICE_LINE_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")


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
            line = line.strip()
            if line:
                choices.append(_CHOICE_LINE_RE.sub("", line))
        choices = choices[:3]
    if "PASSAGE:" in body:
        body = body.split("PASSAGE:", 1)[1]
    return body.strip(), choices
