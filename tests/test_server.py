"""Server tests: REST commit semantics, SSE generation, reproducibility
metadata, and log replication -- all against a deterministic FakeModel."""
import json

import pytest
from fastapi.testclient import TestClient

from core import EventStore, hash_messages
from strategies.models import FakeModel
from strategies.simple_passthrough import SimplePassthrough
from server import StrategyRegistry, create_app


@pytest.fixture
def client():
    store = EventStore()
    registry = StrategyRegistry()
    registry.register(SimplePassthrough(
        FakeModel(reply="She answered: {last}")))
    return TestClient(create_app(store, registry))


@pytest.fixture
def branched(client):
    """A -> B with two storylines C1, C2."""
    a = client.post("/nodes", json={
        "parent": None, "author": "user",
        "content": "The door was open."}).json()
    b = client.post("/nodes", json={
        "parent": a["payload"]["node"], "author": "char",
        "content": "She stepped inside."}).json()
    c1 = client.post("/nodes", json={
        "parent": b["payload"]["node"], "author": "char",
        "content": "Branch one."}).json()
    c2 = client.post("/nodes", json={
        "parent": b["payload"]["node"], "author": "char",
        "content": "Branch two."}).json()
    ids = {k: v["payload"]["node"] for k, v in
           dict(A=a, B=b, C1=c1, C2=c2).items()}
    return client, ids


def parse_sse(body: str):
    """Parse an SSE body into [(event, data)] pairs."""
    out = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        ev = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        out.append((ev, data))
    return out


# -- REST commit semantics ---------------------------------------------------

def test_append_and_branch(branched):
    client, ids = branched
    r = client.get(f"/branch/{ids['C1']}")
    assert r.status_code == 200
    assert [m["content"] for m in r.json()["messages"]] == [
        "The door was open.", "She stepped inside.", "Branch one."]


def test_revise_propagates(branched):
    client, ids = branched
    r = client.post(f"/nodes/{ids['A']}/revise",
                    json={"content": "The door was ajar."})
    assert r.status_code == 200
    for leaf in ("C1", "C2"):
        msgs = client.get(f"/branch/{ids[leaf]}").json()["messages"]
        assert msgs[0]["content"] == "The door was ajar."


def test_diverge_and_undo_via_api(branched):
    client, ids = branched
    r = client.post(f"/nodes/{ids['B']}/diverge",
                    json={"content": "She stepped back.", "move": ids["C2"]})
    assert r.status_code == 200
    tx = r.json()["variant"]["tx"]
    msgs = client.get(f"/branch/{ids['C2']}").json()["messages"]
    assert msgs[1]["content"] == "She stepped back."
    r = client.post(f"/undo/{tx}")
    assert r.status_code == 200 and len(r.json()) == 2
    msgs = client.get(f"/branch/{ids['C2']}").json()["messages"]
    assert msgs[1]["content"] == "She stepped inside."


def test_variant_endpoint(branched):
    client, ids = branched
    r = client.post(f"/nodes/{ids['C2']}/variant",
                    json={"content": "Branch two, rewritten."})
    assert r.status_code == 200
    assert r.json()["kind"] == "variant"
    # the variant is a sibling: B now has three story children
    state = client.get("/state").json()
    assert len(state["nodes"][ids["B"]]["children"]) == 3
    assert state["nodes"][ids["C2"]]["content"] == "Branch two."


def test_invariant_violation_is_409(branched):
    client, ids = branched
    # graft B under its own child C1 -> cycle -> 409 Conflict
    r = client.post(f"/nodes/{ids['A']}/meta",
                    json={"key": "k", "value": 1})
    assert r.status_code == 200
    # unknown node -> 404
    assert client.get("/branch/nope").status_code == 404


def test_state_and_watermark(branched):
    client, ids = branched
    head = client.get("/state").json()["head"]
    client.post(f"/nodes/{ids['A']}/revise",
                json={"content": "The door was ajar."})
    past = client.get("/state", params={"at": head}).json()
    assert past["nodes"][ids["A"]]["content"] == "The door was open."
    now = client.get("/state").json()
    assert now["nodes"][ids["A"]]["content"] == "The door was ajar."
    assert now["head"] == head + 1


def test_events_replay(branched):
    client, _ = branched
    all_events = client.get("/events").json()
    assert len(all_events) == 4
    tail = client.get("/events", params={"after": 2}).json()
    assert [e["seq"] for e in tail] == [3, 4]


def test_stale_flag_in_branch(branched):
    client, ids = branched
    assert client.get(f"/branch/{ids['C1']}").json()["stale"] == []
    client.post(f"/nodes/{ids['B']}/revise",
                json={"content": "She stepped inside, quietly."})
    assert client.get(f"/branch/{ids['C1']}").json()["stale"] == [ids["B"]]


# -- generation ---------------------------------------------------------------

def test_generate_streams_then_commits(branched):
    client, ids = branched
    r = client.post("/generate", json={
        "leaf": ids["C1"], "strategy": "simple_passthrough/0.1",
        "author": "char"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert kinds[-1] == "committed"
    assert all(k == "token" for k in kinds[:-1])
    text = "".join(d["delta"] for k, d in events if k == "token")
    assert text == "She answered: Branch one."
    committed = events[-1][1]["event"]
    assert committed["kind"] == "append"
    assert committed["payload"]["content"] == text
    assert committed["payload"]["parent"] == ids["C1"]


def test_generate_records_reproducibility_meta(branched):
    client, ids = branched
    head = client.get("/state").json()["head"]
    client.post("/generate", json={
        "leaf": ids["C1"], "strategy": "simple_passthrough/0.1"})
    state = client.get("/state").json()
    gen_node = next(n for n in state["nodes"].values()
                    if n["parent"] == ids["C1"])
    meta = gen_node["generation"]
    assert meta["strategy"] == "simple_passthrough/0.1"
    assert meta["model"] == "fake"
    assert meta["watermark"] == head
    # the recorded hash equals the hash of the reconstructed context
    ctx = client.get(f"/branch/{ids['C1']}", params={"at": head}).json()
    class M:  # hash_messages expects Message-like objects
        def __init__(self, d):
            self.author, self.content = d["author"], d["content"]
    assert hash_messages([M(m) for m in ctx["messages"]]) == \
        meta["context_hash"]


def test_generate_unknown_strategy_404(branched):
    client, ids = branched
    r = client.post("/generate", json={
        "leaf": ids["C1"], "strategy": "nonexistent/9.9"})
    assert r.status_code == 404


def test_strategy_registry_endpoint(client):
    assert client.get("/strategies").json() == ["simple_passthrough/0.1"]


def test_truncation_via_params(branched):
    client, ids = branched
    r = client.post("/generate", json={
        "leaf": ids["C1"], "strategy": "simple_passthrough/0.1",
        "params": {"max_messages": 2}})
    events = parse_sse(r.text)
    # prompt was root + last message -> "{last}" still resolved
    text = "".join(d["delta"] for k, d in events if k == "token")
    assert text == "She answered: Branch one."


def test_event_stream_replays_after_seq(branched):
    client, _ = branched
    # follow=false: replay mode terminates after the backlog
    r = client.get("/events/stream", params={"after": 2, "follow": False})
    assert r.status_code == 200
    lines = [l for l in r.text.strip().split("\n") if l]
    assert len(lines) == 4  # 2 events x (event:, data:)
    body = "\n\n".join(["\n".join(lines[:2]), "\n".join(lines[2:4])])
    body = "\n\n".join(["\n".join(lines[:2]), "\n".join(lines[2:4])])
    seqs = [d["seq"] for _, d in parse_sse(body)]
    assert seqs == [3, 4]
