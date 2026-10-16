"""best_of_n: candidate scoring, top-k commit, workspace retention,
single-transaction undo, streamed winner."""
import json

import pytest
from fastapi.testclient import TestClient

from core import EventStore
from strategies.best_of_n import BestOfN, parse_score
from strategies.models import ScriptedModel
from server import StrategyRegistry, create_app


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
    # 3 candidates, then 3 critic scores: C wins (9), A second (7), B out (4)
    registry.register(BestOfN(ScriptedModel([
        "Candidate A.", "Candidate B.", "Candidate C.", "7", "4", "9",
    ])))
    return TestClient(create_app(store, registry))


def generate(client, params=None):
    r = client.post("/generate", json={
        "leaf": "B", "strategy": "best_of_n/0.1", "author": "character",
        "params": params or {"n": 3, "k": 2}})
    assert r.status_code == 200
    return parse_sse(r.text)


def test_top_k_committed_as_siblings(client):
    events = generate(client)
    committed = next(d for k, d in events if k == "committed")
    story = [e for e in committed["events"] if e["payload"]["scope"] == "story"]
    assert [e["payload"]["content"] for e in story] == [
        "Candidate C.", "Candidate A."]  # ranked by score
    # all siblings of the same parent
    assert {e["payload"]["parent"] for e in story} == {"B"}
    # rank + score recorded in generation meta params
    assert story[0]["payload"]["meta"]["params"]["rank"] == 0
    assert story[0]["payload"]["meta"]["params"]["score"] == 9.0
    assert story[1]["payload"]["meta"]["params"]["rank"] == 1


def test_all_candidates_retained_in_workspace(client):
    events = generate(client)
    committed = next(d for k, d in events if k == "committed")
    ws = [e for e in committed["events"]
          if e["payload"]["scope"] == "workspace"]
    assert len(ws) == 3  # including the rejected Candidate B
    contents = {e["payload"]["content"] for e in ws}
    assert "Candidate B." in contents
    scores = {e["payload"]["content"]: e["payload"]["meta"]["params"]["score"]
              for e in ws}
    assert scores["Candidate B."] == 4.0


def test_whole_experiment_is_one_tx(client):
    events = generate(client)
    committed = next(d for k, d in events if k == "committed")
    txs = {e["tx"] for e in committed["events"]}
    assert len(txs) == 1
    # undoing it tombstones every planned node at once
    r = client.post(f"/undo/{txs.pop()}")
    assert len(r.json()) == 5  # 3 workspace + 2 story
    state = client.get("/state").json()
    assert all(state["nodes"][e["payload"]["node"]]["tombstoned"]
               for e in committed["events"])


def test_winner_is_streamed(client):
    events = generate(client)
    text = "".join(d["delta"] for k, d in events if k == "token")
    assert text == "Candidate C."


def test_backward_compat_event_field(client):
    events = generate(client)
    committed = next(d for k, d in events if k == "committed")
    assert committed["event"]["payload"]["content"] == "Candidate C."
    assert committed["event"]["payload"]["scope"] == "story"


def test_watermark_and_hash_on_planned_nodes(client):
    head = client.get("/state").json()["head"]
    events = generate(client)
    committed = next(d for k, d in events if k == "committed")
    for e in committed["events"]:
        meta = e["payload"]["meta"]
        assert meta["strategy"] == "best_of_n/0.1"
        assert meta["watermark"] == head
        assert len(meta["context_hash"]) == 64


def test_workspace_nodes_stay_out_of_story_branches(client):
    generate(client)
    state = client.get("/state").json()
    winner = next(n for n in state["nodes"].values()
                  if n["scope"] == "story" and n["parent"] == "B"
                  and n["content"] == "Candidate C.")
    branch = client.get(f"/branch/{winner['id']}").json()
    assert [m["content"] for m in branch["messages"]] == [
        "The door was open.", "She stepped inside.", "Candidate C."]


# -- score parsing ----------------------------------------------------------

def test_parse_score():
    assert parse_score("9") == 9.0
    assert parse_score("I'd give it a 7.5 out of 10") == 7.5
    assert parse_score("42") == 10.0        # clamped
    assert parse_score("no number here") == 5.0
    assert parse_score("0") == 1.0          # clamped up
