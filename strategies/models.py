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
    """Deterministic but varied stand-in for the demo/preview: cycles
    through candidate continuations; when the last message is authored by
    "critic" (a scoring prompt), cycles through canned scores instead."""

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
        self._scores = itertools.cycle(scores or ["8", "6", "9", "5", "7"])
        self.name = name

    async def stream(self, messages: list[Message],
                     params: dict[str, Any]) -> AsyncIterator[str]:
        text = (next(self._scores) if messages and messages[-1].author == "critic"
                else next(self._replies))
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

    async def stream(self, messages: list[Message],
                     params: dict[str, Any]) -> AsyncIterator[str]:
        role_map = {"user": "user"}
        wire = ([{"role": "system", "content": self.system}]
                if self.system else [])
        wire += [{"role": role_map.get(m.author, "assistant"),
                  "content": m.content} for m in messages]
        resp = await self._litellm.acompletion(
            model=self.name, messages=wire, stream=True,
            **{**self.defaults, **params})
        async for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
