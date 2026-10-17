"""beat_planner: hierarchical plan-then-execute.

Where best_of_n samples in parallel and langgraph_crew loops a pipeline,
this strategy separates *what happens* from *how it is told*:

  1. look for an active ROADMAP: the newest visible workspace node with
     stage "plan" hanging off an ancestor of the leaf, which still has
     undrafted beats on this storyline;
  2. if none (or params.replan), a planner call outlines the next k
     beats as a numbered list -> ONE workspace node. The roadmap is a
     tree citizen: inspectable in the workshop panel, covered by undo,
     and shared by every fork descending from its anchor point;
  3. a drafter call writes ONLY the next beat -> one story node with
     beat/of/plan_id provenance.

The strategy is stateless: which beat comes next is derived from the
tree (story nodes on the path carrying the plan's id), never from
memory. So a beat sheet written three forks ago is picked up correctly,
and undoing a drafted beat makes the planner offer it again.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator
from uuid import uuid4

from core.materialize import Message
from core.state import Node, TreeState

from .best_of_n import collect
from .models import ChatModel
from .simple_passthrough import truncate

_BEAT_NUM_RE = re.compile(r"^\s*\d+[.)]\s+")
_BEAT_MARK_RE = re.compile(r"^\s*(?:\d+[.)]|[-•*])\s+")


def parse_beats(text: str) -> list[str]:
    """A numbered list, one beat per entry. If the model ignored the
    numbering, fall back to one beat per non-empty line (bullet markers
    stripped); a single line is a single beat."""
    lines = [l for l in text.splitlines() if l.strip()]
    numbered = [l for l in lines if _BEAT_NUM_RE.match(l)]
    src = numbered if numbered else lines
    return [b for b in (_BEAT_MARK_RE.sub("", l).strip() for l in src) if b]


def _msg(author: str, content: str) -> Message:
    return Message(node="", author=author, content=content,
                   version=1, pinned=False, scope="story")


def _plan_params(node: Node) -> dict[str, Any]:
    return node.generation.params if node.generation else {}


def _next_beat(state: TreeState, leaf: str, plan: Node) -> int | None:
    """1-based index of the next undrafted beat on this storyline, or
    None when the roadmap is exhausted. Tombstoned beats don't count:
    an undone beat is offered again."""
    pid = _plan_params(plan).get("plan_id")
    beats = _plan_params(plan).get("beats", [])
    drafted = 0
    for nid in state.path_to_root(leaf):
        n = state.nodes[nid]
        if (n.scope == "story" and not n.tombstoned
                and _plan_params(n).get("plan_id") == pid):
            drafted += 1
    nxt = drafted + 1
    return nxt if nxt <= len(beats) else None


def _active_plan(state: TreeState, leaf: str) -> Node | None:
    """Newest usable roadmap anchored on this storyline."""
    path = set(state.path_to_root(leaf))
    plans = [n for n in state.nodes.values()
             if n.scope == "workspace" and n.parent in path
             and _plan_params(n).get("stage") == "plan"
             and not state.is_hidden(n.id)]
    plans.sort(key=lambda n: n.created_seq, reverse=True)
    return next((p for p in plans if _next_beat(state, leaf, p) is not None),
                None)


class BeatPlanner:
    id = "beat_planner/0.1"

    def __init__(self, model: ChatModel):
        self.model = model
        self.model_name = model.name

    def for_model(self, model: ChatModel) -> "BeatPlanner":
        return BeatPlanner(model)

    async def generate(self, ctx, params: dict[str, Any]) -> AsyncIterator[str]:
        n_beats = max(1, int(params.get("beats", 3)))
        author = params.get("author", "character")

        base = truncate(ctx.materialize(), params.get("max_messages"))
        ctx.record_prompt(base)

        state = ctx.tree_at()
        plan = None if params.get("replan") else _active_plan(state, ctx.leaf)

        if plan is None:
            sheet = await collect(self.model.stream(base + [_msg("planner",
                f"Outline the next {n_beats} beats of this story as a "
                f"numbered list — concrete turns (an action, a discovery, "
                f"a reversal), one line each, no commentary.")], {}))
            beats = (parse_beats(sheet) or [sheet.strip() or "Continue."])
            beats = beats[:n_beats]
            ctx.plan_node(
                content=sheet, scope="workspace",
                meta_extra={"stage": "plan", "beats": beats,
                            "plan_id": uuid4().hex})
            # the plan node does not exist yet; re-derive from what we
            # just decided rather than reading the tree
            plan_id = ctx.planned[-1]["meta_extra"]["plan_id"]
            next_beat = 1
        else:
            beats = _plan_params(plan)["beats"]
            plan_id = _plan_params(plan)["plan_id"]
            next_beat = _next_beat(state, ctx.leaf, plan) or 1

        beat_text = beats[next_beat - 1]
        sheet_lines = "\n".join(f"{i}. {b}" for i, b in enumerate(beats, 1))
        draft = await collect(self.model.stream(base + [_msg("drafter",
            f"Upcoming beats:\n{sheet_lines}\n\n"
            f"Write only beat {next_beat}: {beat_text}\n"
            f"Output only the story text.")], {}))

        ctx.plan_node(content=draft, scope="story", author=author,
                      meta_extra={"beat": next_beat, "of": len(beats),
                                  "plan_id": plan_id,
                                  "beat_text": beat_text})

        words = draft.split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "
