"""Workspace inspection: /workspace/{node} returns the artifacts of the
same generation run (same tx), in execution order."""
import json

import pytest
from fastapi.testclient import TestClient

from core import EventStore
from strategies.best_of_n import BestOfN
from strategies.models import ScriptedModel
from server import StrategyRegistry, create_app

from test_strategies import parse_sse


@pytest.fixture
def client():
    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    store.append_node("A", "character", "She stepped inside.", node_id="B")
    registry = StrategyRegistry()
    registry.register(BestOfN(ScriptedModel([
        "Candidate A.", "Candidate B.", "Candidate C.", "7", "4", "9",
    ])))
    return TestClient(create_app(store, registry))


def winner_id(client) -> str:
    state = client.get("/state").json()
    return next(n["id"] for n in state["nodes"].values()
                if n["scope"] == "story" and n["content"] == "Candidate C.")


def test_workspace_artifacts_of_generation_run(client):
    client.post("/generate", json={
        "leaf": "B", "strategy": "best_of_n/0.1",
        "params": {"n": 3, "k": 2}})
    r = client.get(f"/workspace/{winner_id(client)}")
    assert r.status_code == 200
    arts = r.json()["artifacts"]
    # all three candidates, ranked order (the strategy planned them so)
    assert [a["content"] for a in arts] == [
        "Candidate C.", "Candidate A.", "Candidate B."]
    assert [a["generation"]["params"]["score"] for a in arts] == [9.0, 7.0, 4.0]
    assert all(a["scope"] == "workspace" for a in arts)


def test_artifacts_share_the_story_nodes_tx(client):
    events = parse_sse(client.post("/generate", json={
        "leaf": "B", "strategy": "best_of_n/0.1",
        "params": {"n": 3, "k": 2}}).text)
    committed = next(d for k, d in events if k == "committed")
    tx = committed["events"][0]["tx"]
    r = client.get(f"/workspace/{winner_id(client)}").json()
    assert r["tx"] == tx
    assert all(a["tx"] == tx for a in r["artifacts"])


def test_no_workspace_for_handwritten_node(client):
    r = client.get("/workspace/B")
    assert r.status_code == 200
    assert r.json()["artifacts"] == []


def test_workspace_unknown_node_404(client):
    assert client.get("/workspace/nope").status_code == 404


def test_crew_stages_in_execution_order(client):
    langgraph = pytest.importorskip("langgraph")
    from strategies.langgraph_crew import LangGraphCrew

    store = EventStore()
    store.append_node(None, "you", "The door was open.", node_id="A")
    registry = StrategyRegistry()
    registry.register(LangGraphCrew(ScriptedModel([
        "direction", "draft one", "VERDICT: REVISE — pacing. 6/10",
        "draft two", "VERDICT: PASS — lands. 9/10",
    ])))
    c = TestClient(create_app(store, registry))
    events = parse_sse(c.post("/generate", json={
        "leaf": "A", "strategy": "langgraph_crew/0.1"}).text)
    committed = next(d for k, d in events if k == "committed")
    story = next(e for e in committed["events"]
                 if e["payload"]["scope"] == "story")
    arts = c.get(f"/workspace/{story['payload']['node']}").json()["artifacts"]
    assert [a["generation"]["params"]["stage"] for a in arts] == [
        "direct", "draft", "critique", "revision", "critique"]
    assert arts[-1]["generation"]["params"]["verdict"] == "PASS"
