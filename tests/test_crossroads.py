"""crossroads: PASSAGE:/CHOICES: parsing, choice/guidance provenance,
one-tx commits with image SetMeta, degradation paths."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from core import EventStore
from server import StrategyRegistry, create_app
from strategies.crossroads import parse_crossroads_output


def test_parses_standard_format():
    text = ("PASSAGE:\nShe stepped inside, and the dust rose to meet her.\n\n"
            "CHOICES:\n- Light a match\n- Call into the dark\n- Take the stairs")
    passage, choices = parse_crossroads_output(text)
    assert passage == "She stepped inside, and the dust rose to meet her."
    assert choices == ["Light a match", "Call into the dark", "Take the stairs"]


def test_numbered_choices_accepted():
    text = "PASSAGE:\nA.\n\nCHOICES:\n1. one\n2. two\n3) three"
    assert parse_crossroads_output(text)[1] == ["one", "two", "three"]


def test_missing_choices_block_degrades():
    passage, choices = parse_crossroads_output("PASSAGE:\nJust the text.")
    assert passage == "Just the text."
    assert choices == []


def test_missing_passage_marker_uses_whole_reply():
    passage, choices = parse_crossroads_output("No markers at all.")
    assert passage == "No markers at all."
    assert choices == []


def test_extra_choices_trimmed_to_three():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n- b\n- c\n- d"
    assert parse_crossroads_output(text)[1] == ["a", "b", "c"]


def test_empty_choice_lines_skipped():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n\n- b\n"
    assert parse_crossroads_output(text)[1] == ["a", "b"]


def test_bare_marker_choice_lines_skipped():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n-\n- b\n- c"
    assert parse_crossroads_output(text)[1] == ["a", "b", "c"]


def parse_sse(body: str):
    out = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        out.append((lines[0].removeprefix("event: "),
                    json.loads(lines[1].removeprefix("data: "))))
    return out


class _StubStrategy:
    """Plans fixed nodes; lets us drive the planned-commit path directly."""

    id = "stub/0.1"
    model_name = "stub"

    def __init__(self, planned):
        self._planned = planned

    async def generate(self, ctx, params):
        for kw in self._planned:
            ctx.plan_node(**kw)
        yield "done"


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'


def _stub_client(planned):
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(_StubStrategy(planned))
    return TestClient(create_app(store, registry))


def test_planned_node_carries_meta_and_image():
    client = _stub_client([dict(
        content="A passage.", scope="story",
        meta={"kind": "test"}, image=_SVG)])
    events = parse_sse(client.post("/generate", json={
        "leaf": "A", "strategy": "stub/0.1"}).text)
    committed = next(d for k, d in events if k == "committed")["events"]
    kinds = [e["kind"] for e in committed]
    assert kinds == ["append", "set_meta", "set_meta"]
    assert len({e["tx"] for e in committed}) == 1
    node_id = committed[0]["payload"]["node"]
    meta_events = {e["payload"]["key"]: e["payload"]["value"]
                   for e in committed if e["kind"] == "set_meta"}
    assert meta_events["kind"] == "test"
    ref = meta_events["image"]
    r = client.get(f"/media/{ref}")
    assert r.status_code == 200 and r.content == _SVG
    state = client.get("/state").json()
    assert state["nodes"][node_id]["meta"]["image"] == ref
    assert state["nodes"][node_id]["meta"]["kind"] == "test"


def test_planned_node_without_image_stays_plain():
    client = _stub_client([dict(content="Plain.", scope="story")])
    events = parse_sse(client.post("/generate", json={
        "leaf": "A", "strategy": "stub/0.1"}).text)
    committed = next(d for k, d in events if k == "committed")["events"]
    assert [e["kind"] for e in committed] == ["append"]


def test_undo_reverts_node_and_image_meta():
    client = _stub_client([dict(content="A passage.", scope="story", image=_SVG)])
    events = parse_sse(client.post("/generate", json={
        "leaf": "A", "strategy": "stub/0.1"}).text)
    committed = next(d for k, d in events if k == "committed")["events"]
    tx = committed[0]["tx"]
    node_id = committed[0]["payload"]["node"]
    client.post(f"/undo/{tx}")
    node = client.get("/state").json()["nodes"][node_id]
    assert node["tombstoned"]
    assert node["meta"].get("image") is None


def test_bad_image_payload_yields_sse_error():
    client = _stub_client([dict(content="A passage.", scope="story",
                                image=b"\x89GIF89a not an image")])
    events = parse_sse(client.post("/generate", json={
        "leaf": "A", "strategy": "stub/0.1"}).text)
    kinds = [k for k, _ in events]
    assert "error" in kinds
    assert "committed" not in kinds


from core.materialize import Message
from strategies.models import DemoModel, LiteLLMModel


def _msg(author, content="x"):
    return Message(node="", author=author, content=content,
                   version=1, pinned=False, scope="story")


def test_guide_maps_to_user_role():
    assert LiteLLMModel.role_of("guide") == "user"


def test_demo_model_answers_guide_turns_in_crossroads_format():
    async def run():
        model = DemoModel()
        return "".join([t async for t in model.stream([_msg("guide")], {})])
    text = asyncio.run(run())
    passage, choices = parse_crossroads_output(text)
    assert passage
    assert len(choices) == 3


def test_demo_model_director_cycle_unchanged():
    async def run():
        model = DemoModel()
        return "".join([t async for t in model.stream([_msg("director")], {})])
    text = asyncio.run(run())
    assert "PASSAGE:" not in text  # director stays a direction line
