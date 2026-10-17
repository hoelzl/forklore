"""MediaStore (content-addressed image bytes) and DemoImageModel."""
import asyncio

import pytest

from strategies.images import DemoImageModel


def _gen(model, prompt):
    return asyncio.run(_collect(model, prompt))


async def _collect(model, prompt):
    return await model.generate(prompt, {})


def test_demo_image_is_deterministic():
    model = DemoImageModel()
    assert _gen(model, "a lighthouse at night") == _gen(model, "a lighthouse at night")


def test_demo_image_is_svg_with_title_word():
    svg = _gen(DemoImageModel(), "the fog pressed its face").decode()
    assert svg.startswith("<svg")
    assert "The" in svg  # first content word, titled


def test_demo_image_varies_with_prompt():
    model = DemoImageModel()
    assert _gen(model, "one story") != _gen(model, "another story")


def test_demo_image_metadata():
    assert DemoImageModel.ext == "svg"
    assert DemoImageModel.name
