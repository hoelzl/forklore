"""MediaStore (content-addressed image bytes) and DemoImageModel."""
import asyncio

import pytest

from server.media import MediaStore
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


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_save_and_read_roundtrip_memory():
    store = MediaStore()
    name = store.save(_SVG)
    assert name.endswith(".svg")
    data, content_type = store.read(name)
    assert data == _SVG
    assert content_type == "image/svg+xml"


def test_save_and_read_roundtrip_disk(tmp_path):
    store = MediaStore(str(tmp_path))
    name = store.save(_PNG)
    assert name.endswith(".png")
    assert (tmp_path / name).read_bytes() == _PNG
    data, content_type = MediaStore(str(tmp_path)).read(name)
    assert data == _PNG
    assert content_type == "image/png"


def test_save_is_content_addressed():
    store = MediaStore()
    assert store.save(_SVG) == store.save(_SVG)


def test_unsupported_payload_rejected():
    with pytest.raises(ValueError):
        MediaStore().save(b"not an image")


def test_traversal_and_bad_names_rejected():
    store = MediaStore()
    for bad in ("../etc/passwd", "a/b.svg", "ABC.svg", "x" * 64 + ".svg",
                "x" * 64 + ".gif", ""):
        assert store.read(bad) is None


def test_unknown_name_reads_none():
    store = MediaStore()
    assert store.read("a" * 64 + ".svg") is None


def test_trailing_newline_name_rejected():
    store = MediaStore()
    assert store.read("a" * 64 + ".svg\n") is None
