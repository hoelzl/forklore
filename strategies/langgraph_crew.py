"""langgraph_crew: a director/drafter/critic orchestration, implemented as
a LangGraph StateGraph inside the strategy boundary.

    direct ──> draft ──> critique ──┐
                 ▲                  │ verdict
                 └── revise ◀── REVISE (up to max_revisions)
                                    │ PASS (or cap reached)
                                  finish

Every intermediate artifact — the director's beat, each draft, each
critique — is planned as a workspace node; only the accepted text becomes
a story node. The server commits the whole run as ONE transaction, so a
full orchestration is a single undo unit and carries the usual
watermark + context_hash provenance.

LangGraph is an implementation detail of this strategy: it never touches
core, and its state lives only for the duration of generate(). Persistence
of scratch work is forklore's job (workspace nodes), not a checkpointer's.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, TypedDict

from core.materialize import Message

from .best_of_n import collect
from .models import ChatModel
from .simple_passthrough import truncate

_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|REVISE)", re.IGNORECASE)


def parse_verdict(text: str) -> str:
    """PASS / REVISE. Unparseable critiques are lenient (PASS): the
    revision cap bounds the loop anyway, and a stuck critic should not
    spin the drafter."""
    m = _VERDICT_RE.search(text)
    return m.group(1).upper() if m else "PASS"


def _msg(author: str, content: str) -> Message:
    return Message(node="", author=author, content=content,
                   version=1, pinned=False, scope="story")


class CrewState(TypedDict):
    direction: str
    draft: str
    critique: str
    verdict: str
    rounds: int
    drafts: list[str]
    critiques: list[str]


class LangGraphCrew:
    id = "langgraph_crew/0.1"

    def __init__(self, model: ChatModel):
        self.model = model
        self.model_name = model.name

    def for_model(self, model: ChatModel) -> "LangGraphCrew":
        """A copy bound to a different model (all three roles share it)."""
        return LangGraphCrew(model)

    # -- graph nodes -------------------------------------------------------

    async def _direct(self, state: CrewState, base: list[Message]) -> dict:
        prompt = base + [_msg("director",
            "In one or two sentences, what should happen next in this "
            "story? Name a concrete beat — an action, a discovery, a turn — "
            "not a summary.")]
        return {"direction": await collect(self.model.stream(prompt, {}))}

    async def _draft(self, state: CrewState, base: list[Message]) -> dict:
        prompt = base + [_msg("drafter",
            f"Direction for the next beat: {state['direction']}\n\n"
            "Write the continuation — one to three paragraphs, in the "
            "story's established voice. Output only the story text.")]
        draft = await collect(self.model.stream(prompt, {}))
        return {"draft": draft, "drafts": state["drafts"] + [draft]}

    async def _critique(self, state: CrewState, base: list[Message]) -> dict:
        tail = "\n\n".join(m.content for m in base[-4:])
        prompt = [_msg("critic",
            f"Story so far:\n{tail}\n\n"
            f"Draft continuation:\n{state['draft']}\n\n"
            "Assess the draft: voice, coherence with the story so far, "
            "forward momentum. Your first line must be exactly "
            "VERDICT: PASS or VERDICT: REVISE, followed by one or two "
            "sentences of concrete feedback.")]
        critique = await collect(self.model.stream(prompt, {}))
        return {"critique": critique,
                "verdict": parse_verdict(critique),
                "critiques": state["critiques"] + [critique]}

    async def _revise(self, state: CrewState, base: list[Message]) -> dict:
        prompt = base + [_msg("drafter",
            f"Direction for the next beat: {state['direction']}\n\n"
            f"Your draft:\n{state['draft']}\n\n"
            f"Critic's feedback:\n{state['critique']}\n\n"
            "Revise the draft in response to the feedback. Output only "
            "the revised story text.")]
        draft = await collect(self.model.stream(prompt, {}))
        return {"draft": draft, "rounds": state["rounds"] + 1,
                "drafts": state["drafts"] + [draft]}

    # -- the strategy entry point -------------------------------------------

    async def generate(self, ctx, params: dict[str, Any]) -> AsyncIterator[str]:
        from langgraph.graph import END, StateGraph  # per-strategy dep

        max_revisions = int(params.get("max_revisions", 2))
        author = params.get("author", "character")

        base = truncate(ctx.materialize(), params.get("max_messages"))
        ctx.record_prompt(base)

        # NB: plain lambdas would hide the coroutine from langgraph's
        # async detection (iscoroutinefunction), so bind explicitly.
        async def direct(s: CrewState) -> dict[str, Any]:
            return await self._direct(s, base)

        async def draft(s: CrewState) -> dict[str, Any]:
            return await self._draft(s, base)

        async def critique(s: CrewState) -> dict[str, Any]:
            return await self._critique(s, base)

        async def revise(s: CrewState) -> dict[str, Any]:
            return await self._revise(s, base)

        g: StateGraph = StateGraph(CrewState)
        g.add_node("direct", direct)
        g.add_node("draft", draft)
        g.add_node("critique", critique)
        g.add_node("revise", revise)
        g.set_entry_point("direct")
        g.add_edge("direct", "draft")
        g.add_edge("draft", "critique")
        g.add_conditional_edges(
            "critique",
            lambda s: "revise" if (s["verdict"] == "REVISE"
                                   and s["rounds"] < max_revisions) else "finish",
            {"revise": "revise", "finish": END})
        g.add_edge("revise", "critique")

        result: CrewState = await g.compile().ainvoke(CrewState(
            direction="", draft="", critique="", verdict="",
            rounds=0, drafts=[], critiques=[]))

        # persist every artifact as workspace nodes (inspection > deletion)
        ctx.plan_node(content=result["direction"], scope="workspace",
                      meta_extra={"stage": "direct"})
        for i, draft in enumerate(result["drafts"]):
            ctx.plan_node(content=draft, scope="workspace",
                          meta_extra={"stage": "draft" if i == 0 else "revision",
                                      "round": i})
            if i < len(result["critiques"]):
                critique = result["critiques"][i]
                ctx.plan_node(content=critique, scope="workspace",
                              meta_extra={"stage": "critique", "round": i,
                                          "verdict": parse_verdict(critique)})

        # the accepted text becomes the story node
        ctx.plan_node(content=result["draft"], scope="story", author=author,
                      meta_extra={"rounds": result["rounds"],
                                  "verdict": result["verdict"],
                                  "direction": result["direction"]})

        words = result["draft"].split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "
