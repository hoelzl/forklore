"""Branch management: named storylines via SetMeta — list, name, rename,
unname, prune (one tx), undo."""
import pytest
from fastapi.testclient import TestClient

from core import EventStore
from strategies.models import FakeModel
from strategies.simple_passthrough import SimplePassthrough
from server import StrategyRegistry, create_app


@pytest.fixture
def client():
    store = EventStore()
    registry = StrategyRegistry()
    registry.register(SimplePassthrough(FakeModel()))
    c = TestClient(create_app(store, registry))
    store.append_node(None, "you", "The door was open.", node_id="A")
    store.append_node("A", "character", "She stepped inside.", node_id="B")
    store.append_node("B", "character", "Branch one.", node_id="C1")
    store.append_node("B", "character", "Branch two.", node_id="C2")
    return c


def names(client):
    return [b["name"] for b in client.get("/branches").json()]


def test_name_and_list(client):
    r = client.post("/branches", json={"name": "mirror", "node": "C1"})
    assert r.status_code == 200
    branches = client.get("/branches").json()
    assert branches == [{"name": "mirror", "node": "C1", "created_seq": 3,
                         "depth": 3, "hidden": False}]


def test_duplicate_name_rejected(client):
    client.post("/branches", json={"name": "mirror", "node": "C1"})
    r = client.post("/branches", json={"name": "mirror", "node": "C2"})
    assert r.status_code == 409


def test_naming_a_named_node_renames(client):
    client.post("/branches", json={"name": "mirror", "node": "C1"})
    client.post("/branches", json={"name": "glass", "node": "C1"})
    assert names(client) == ["glass"]


def test_unname(client):
    client.post("/branches", json={"name": "mirror", "node": "C1"})
    assert client.delete("/branches/mirror").status_code == 200
    assert names(client) == []
    assert client.delete("/branches/mirror").status_code == 404


def test_prune_is_one_tx_and_undoes_as_one(client):
    client.post("/branches", json={"name": "mirror", "node": "C1"})
    r = client.post("/branches/mirror/prune")
    assert r.status_code == 200
    events = r.json()
    assert [e["kind"] for e in events] == ["tombstone", "set_meta"]
    assert {e["tx"] for e in events} == {events[0]["tx"]}  # one tx

    state = client.get("/state").json()
    assert state["nodes"]["C1"]["tombstoned"] is True
    assert names(client) == []

    # one undo restores both the node and its name
    client.post(f"/undo/{events[0]['tx']}")
    state = client.get("/state").json()
    assert state["nodes"]["C1"]["tombstoned"] is False
    assert names(client) == ["mirror"]


def test_prune_unknown_branch_404(client):
    assert client.post("/branches/ghost/prune").status_code == 404


def test_name_unknown_node_404(client):
    r = client.post("/branches", json={"name": "mirror", "node": "ZZ"})
    assert r.status_code == 404


def test_empty_name_422(client):
    r = client.post("/branches", json={"name": "  ", "node": "C1"})
    assert r.status_code == 422
