"""langgraph_crew: the director/drafter/critic loop, workspace artifacts,
single-transaction commit, verdict parsing."""
import json

import pytest
from fastapi.testclient import TestClient

from core import EventStore
from strategies.langgraph_crew import LangGraphCrew, parse_verdict
from strategies.models import ScriptedModel
from server import StrategyRegistry, create_app

pytest.importorskip("langgraph", reason="langgraph not installed")


def parse_sse(body: str):
    out = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        out.append((lines[0].removeprefix("event: "),
                    json.loads(lines[1].removeprefix("data: "))))
    return out


@pytest.fixture
def client():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    store.append_node("A", "character", "She stepped inside.", node_id="B")
    registry = StrategyRegistry()
    # script: direction, draft 1, critique REVISE, revision, critique PASS
    registry.register(LangGraphCrew(ScriptedModel([
        "She should find the keeper's final note.",
        "Draft one: she finds a note pinned to the wall.",
        "VERDICT: REVISE — too flat; give the note teeth.",
        "Draft two: the note warns her, in the keeper's own hand, "
        "that the light must stay dark.",
        "VERDICT: PASS — the beat turns the scene.",
    ])))
    return TestClient(create_app(store, registry))


def generate(client, params=None):
    r = client.post("/generate", json={
        "leaf": "B", "strategy": "langgraph_crew/0.1", "author": "character",
        "params": params or {}})
    assert r.status_code == 200
    return parse_sse(r.text)


def committed(events):
    return next(d for k, d in events if k == "committed")


def test_revise_loop_runs_and_accepts(client):
    events = generate(client)
    story = [e for e in committed(events)["events"]
             if e["payload"]["scope"] == "story"]
    assert len(story) == 1
    assert story[0]["payload"]["content"].startswith("Draft two:")
    meta = story[0]["payload"]["meta"]["params"]
    assert meta["rounds"] == 1
    assert meta["verdict"] == "PASS"
    assert meta["direction"] == "She should find the keeper's final note."


def test_all_artifacts_in_workspace(client):
    events = generate(client)
    ws = [e["payload"] for e in committed(events)["events"]
          if e["payload"]["scope"] == "workspace"]
    stages = [p["meta"]["params"]["stage"] for p in ws]
    assert stages == ["direct", "draft", "critique", "revision", "critique"]
    verdicts = [p["meta"]["params"].get("verdict") for p in ws
                if p["meta"]["params"]["stage"] == "critique"]
    assert verdicts == ["REVISE", "PASS"]


def test_run_is_one_tx_and_undoes_as_one(client):
    events = generate(client)
    evs = committed(events)["events"]
    assert len({e["tx"] for e in evs}) == 1
    r = client.post(f"/undo/{evs[0]['tx']}")
    assert len(r.json()) == len(evs)  # 5 workspace + 1 story
    state = client.get("/state").json()
    assert all(state["nodes"][e["payload"]["node"]]["tombstoned"]
               for e in evs)


def test_final_text_is_streamed(client):
    events = generate(client)
    text = "".join(d["delta"] for k, d in events if k == "token")
    assert text.startswith("Draft two:")


def test_revision_cap_bounds_loop():
    # a critic that never passes must still terminate
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(LangGraphCrew(ScriptedModel([
        "Direction.", "Draft one.", "VERDICT: REVISE — again.",
        "Draft two.", "VERDICT: REVISE — still no.",
        "Draft three.", "VERDICT: REVISE — never satisfied.",
    ])))
    client = TestClient(create_app(store, registry))
    r = client.post("/generate", json={
        "leaf": "A", "strategy": "langgraph_crew/0.1",
        "params": {"max_revisions": 2}})
    assert r.status_code == 200
    events = parse_sse(r.text)
    story = [e for e in committed(events)["events"]
             if e["payload"]["scope"] == "story"]
    assert story[0]["payload"]["content"] == "Draft three."
    assert story[0]["payload"]["meta"]["params"]["rounds"] == 2


def test_parse_verdict():
    assert parse_verdict("VERDICT: PASS — clean.") == "PASS"
    assert parse_verdict("verdict: revise — tighten.") == "REVISE"
    assert parse_verdict("no verdict given") == "PASS"  # lenient
