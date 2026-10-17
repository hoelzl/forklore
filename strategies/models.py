"""Model clients: the thin layer between strategies and LLM providers.

A ChatModel is just an async token streamer over a message list. Provider
diversity (OpenAI / Anthropic / OpenRouter / local) is a strategy-layer
concern; core never sees it. `LiteLLMModel` is the production option;
`FakeModel` is deterministic, for tests and offline development.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from core.materialize import Message


class ChatModel(Protocol):
    name: str

    def stream(self, messages: list[Message],
               params: dict[str, Any]) -> AsyncIterator[str]: ...


class FakeModel:
    """Deterministic stand-in: echoes a canned reply, one word per token.
    If the reply contains "{last}", it is filled with the last message's
    content -- handy for checking that context assembly works end to end."""

    def __init__(self, reply: str = "The story continues.", name: str = "fake"):
        self.reply = reply
        self.name = name

    async def stream(self, messages: list[Message],
                     params: dict[str, Any]) -> AsyncIterator[str]:
        text = self.reply.replace("{last}", messages[-1].content if messages
                                  else "")
        words = text.split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "


class ScriptedModel:
    """Test double: returns the next reply from a fixed script, one per
    stream call. Exhaustion is a test bug and fails loudly."""

    def __init__(self, replies: list[str], name: str = "scripted"):
        self._replies = iter(replies)
        self.name = name

    async def stream(self, messages: list[Message],
                     params: dict[str, Any]) -> AsyncIterator[str]:
        yield next(self._replies)


class DemoModel:
    """Deterministic but varied stand-in for the demo/preview. Cycles
    through candidate continuations for narrative turns, directions for
    "director" turns, and verdict-with-score lines for "critic" turns
    (the trailing "N/10" keeps best_of_n's score parser working)."""

    def __init__(self, replies: list[str] | None = None,
                 scores: list[str] | None = None, name: str = "demo"):
        import itertools
        self._replies = itertools.cycle(replies or [
            "The fog pressed its face to the glass, and Mara understood "
            "that the light had never been meant for the ships.",
            "She laughed — one short note — and began to climb, the way "
            "you answer a dare you have waited eleven years to hear.",
            "Below her, the door closed with a sound like a held breath "
            "let go. The keeper's log had not mentioned that.",
            "\"Eleven years is nothing,\" the blue flame said, in the "
            "voice of the last keeper. \"I have been dark for longer.\"",
            "Mara struck the match anyway. Some instructions are only "
            "ever written to be disobeyed.",
        ])
        self._verdicts = itertools.cycle(scores or [
            "VERDICT: REVISE — the image is strong but the pacing sags. 6/10",
            "VERDICT: PASS — the beat lands cleanly and turns the scene. 9/10",
            "VERDICT: REVISE — too much is explained; trust the reader. 5/10",
            "VERDICT: PASS — quiet, exact, and forward-moving. 8/10",
        ])
        self._directions = itertools.cycle([
            "Let Mara discover that the log's last entry is dated tomorrow.",
            "Turn the screw: the blue flame answers a question she did "
            "not ask aloud.",
            "Give the lighthouse a want of its own, and let Mara feel it.",
        ])
        self._beat_sheets = itertools.cycle([
            "1. Mara finds the log's last entry is dated tomorrow.\n"
            "2. The blue flame answers in the last keeper's voice.\n"
            "3. She douses the lamp herself — and the fog answers.",
            "1. The stairs continue past the lamp room, down into rock.\n"
            "2. Below: a second light, burning blue, and a chair still warm.\n"
            "3. The chair's occupant returns — wearing Mara's face.",
        ])
        self.name = name

    async def stream(self, messages: list[Message],
                     params: dict[str, Any]) -> AsyncIterator[str]:
        last = messages[-1].author if messages else ""
        if last == "critic":
            text = next(self._verdicts)
        elif last == "director":
            text = next(self._directions)
        elif last == "planner":
            text = next(self._beat_sheets)
        else:
            text = next(self._replies)
        words = text.split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "


class LiteLLMModel:
    """Production client over litellm (pip install litellm). Maps forklore
    authors onto chat roles: the acting user is "user", everything else is
    "assistant"; a system prompt goes first if configured."""

    def __init__(self, model: str, system: str | None = None, **defaults):
        import litellm  # optional dependency, imported lazily
        self._litellm = litellm
        self.name = model
        self.system = system
        self.defaults = defaults

    @staticmethod
    def role_of(author: str) -> str:
        """Narrative voices keep their dialogue roles; meta-roles used by
        orchestrating strategies (director/critic/drafter instructions)
        are mapped to user turns, which is how instruction prompts behave
        best on chat APIs."""
        if author == "system":
            return "system"
        if author in ("you", "user", "director", "critic", "drafter",
                      "planner"):
            return "user"
        return "assistant"

    async def stream(self, messages: list[Message],
                     params: dict[str, Any]) -> AsyncIterator[str]:
        wire = ([{"role": "system", "content": self.system}]
                if self.system else [])
        wire += [{"role": self.role_of(m.author), "content": m.content}
                 for m in messages]
        resp = await self._litellm.acompletion(
            model=self.name, messages=wire, stream=True,
            **{**self.defaults, **params})
        async for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
