"""crossroads: PASSAGE:/CHOICES: parsing, choice/guidance provenance,
one-tx commits with image SetMeta, degradation paths."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from core import EventStore
from server import StrategyRegistry, create_app
from server.context import StoreBranchContext
from strategies.crossroads import Crossroads, parse_crossroads_output
from strategies.images import DemoImageModel
from strategies.models import ScriptedModel


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


_REPLY = ("PASSAGE:\nShe stepped inside, and the dust rose to meet her.\n\n"
          "CHOICES:\n- Light a match\n- Call into the dark\n- Take the stairs")


@pytest.fixture
def client():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(ScriptedModel([_REPLY]), DemoImageModel()))
    return TestClient(create_app(store, registry))


def _generate(client, params=None):
    r = client.post("/generate", json={
        "leaf": "A", "strategy": "crossroads/0.1", "author": "character",
        "params": params or {}})
    assert r.status_code == 200
    return parse_sse(r.text)


def test_turn_commits_passage_choices_and_image(client):
    events = _generate(client, {"choice": "Open it", "guidance": "keep it quiet"})
    committed = next(d for k, d in events if k == "committed")["events"]
    story = [e for e in committed
             if e["kind"] == "append" and e["payload"]["scope"] == "story"]
    assert len(story) == 1
    payload = story[0]["payload"]
    assert payload["content"] == "She stepped inside, and the dust rose to meet her."
    params = payload["meta"]["params"]
    assert params["choices"] == ["Light a match", "Call into the dark",
                                 "Take the stairs"]
    assert params["choice"] == "Open it"
    assert params["guidance"] == "keep it quiet"
    assert params["image_prompt"].startswith("She stepped inside")
    assert params["image_prompt"].endswith("— illustration for a story passage")
    # image committed in the same tx, referenced from node meta
    meta_events = [e for e in committed if e["kind"] == "set_meta"
                   and e["payload"]["key"] == "image"]
    assert len(meta_events) == 1
    assert meta_events[0]["tx"] == story[0]["tx"]
    ref = meta_events[0]["payload"]["value"]
    assert client.get(f"/media/{ref}").status_code == 200


def test_passage_is_streamed(client):
    events = _generate(client)
    text = "".join(d["delta"] for k, d in events if k == "token")
    assert text == "She stepped inside, and the dust rose to meet her."


def test_malformed_reply_degrades_to_free_form_only():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(
        ScriptedModel(["A plain reply with no structure at all."]),
        DemoImageModel()))
    client = TestClient(create_app(store, registry))
    events = _generate(client)
    committed = next(d for k, d in events if k == "committed")["events"]
    story = [e for e in committed
             if e["kind"] == "append" and e["payload"]["scope"] == "story"]
    assert story[0]["payload"]["content"] == \
        "A plain reply with no structure at all."
    assert story[0]["payload"]["meta"]["params"]["choices"] == []


def test_image_failure_still_commits_the_node():
    class FailingImage:
        name = "failing"
        ext = "svg"

        async def generate(self, prompt, params):
            raise RuntimeError("provider down")

    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(ScriptedModel([_REPLY]), FailingImage()))
    client = TestClient(create_app(store, registry))
    events = _generate(client)
    committed = next(d for k, d in events if k == "committed")["events"]
    kinds = [e["kind"] for e in committed]
    assert kinds == ["append"]  # no image SetMeta
    assert committed[0]["payload"]["content"].startswith("She stepped inside")
    params = committed[0]["payload"]["meta"]["params"]
    assert params["image_error"] == "provider down"


def test_guidance_in_prompt_but_never_in_story():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    strategy = Crossroads(ScriptedModel([_REPLY]), DemoImageModel())
    ctx = StoreBranchContext(store, "A")

    async def run():
        return [t async for t in strategy.generate(
            ctx, {"choice": "Open it", "guidance": "keep it quiet"})]

    asyncio.run(run())
    prompt = ctx.prompt or []
    assert prompt[-1].author == "guide"
    assert "keep it quiet" in prompt[-1].content
    assert "Open it" in prompt[-1].content
    assert all("keep it quiet" not in m.content
               for m in store.materialize("A"))


def test_steering_briefs_prepended_as_director_messages():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    store.append_node("A", "you", "Tone: gothic, quiet, exact.",
                      node_id="S", scope="workspace")
    store.set_meta("S", "kind", "steering")
    store.append_node("S", "character", "She stepped inside.", node_id="B")
    strategy = Crossroads(ScriptedModel([_REPLY]), DemoImageModel())
    ctx = StoreBranchContext(store, "B")

    async def run():
        return [t async for t in strategy.generate(ctx, {})]

    asyncio.run(run())
    prompt = ctx.prompt or []
    directors = [m for m in prompt if m.author == "director"]
    assert len(directors) == 1
    assert "Tone: gothic, quiet, exact." in directors[0].content
    assert prompt[-1].author == "guide"


def test_empty_passage_yields_sse_error():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(Crossroads(
        ScriptedModel(["PASSAGE:\n\nCHOICES:\n- a\n- b\n- c"]),
        DemoImageModel()))
    client = TestClient(create_app(store, registry))
    events = _generate(client)
    kinds = [k for k, _ in events]
    assert "error" in kinds
    assert "committed" not in kinds
