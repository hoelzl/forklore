"""Image models: the visual counterpart of ChatModel.

Provider diversity is a strategy-layer concern, exactly like chat models:
`DemoImageModel` keeps the app fully offline and deterministic;
`LiteLLMImageModel` is the production option, lazily imported.
"""
from __future__ import annotations

import hashlib
from typing import Any, Protocol


class ImageModel(Protocol):
    name: str
    ext: str  # media file extension: "svg" | "png"

    async def generate(self, prompt: str, params: dict[str, Any]) -> bytes: ...


class DemoImageModel:
    """Deterministic offline cover plate: an SVG gradient derived from
    the prompt hash, titled with the prompt's first content word."""

    name = "demo-image"
    ext = "svg"

    async def generate(self, prompt: str, params: dict[str, Any]) -> bytes:
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        hue1 = int(digest[:4], 16) % 360
        hue2 = (hue1 + 40 + int(digest[4:6], 16) % 80) % 360
        word = next(
            (w.strip(".,;:\"'()[]").title() for w in prompt.split()
             if w.strip(".,;:\"'()[]").isalpha()),
            "Passage")
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="hsl({hue1},45%,22%)"/>'
            f'<stop offset="1" stop-color="hsl({hue2},60%,45%)"/>'
            '</linearGradient></defs>'
            '<rect width="640" height="360" fill="url(#g)"/>'
            '<text x="32" y="328" font-family="Georgia, serif" font-size="28" '
            f'fill="rgba(255,255,255,0.85)" font-style="italic">{word}</text>'
            '</svg>')
        return svg.encode()


class LiteLLMImageModel:
    """Provider-backed images via litellm (pip install litellm).

    Requires a provider that returns b64_json; URL-only responses raise
    RuntimeError rather than fetching (no hidden network reads)."""

    ext = "png"

    def __init__(self, model: str, **defaults):
        import litellm  # optional dependency, imported lazily
        self._litellm = litellm
        self.name = model
        self.defaults = defaults

    async def generate(self, prompt: str, params: dict[str, Any]) -> bytes:
        import base64
        resp = await self._litellm.aimage_generation(
            model=self.name, prompt=prompt,
            **{**self.defaults, **params})
        b64 = resp.data[0].b64_json
        if not b64:
            raise RuntimeError(
                f"image model {self.name!r} returned no b64_json payload")
        return base64.b64decode(b64)
