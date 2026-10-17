"""ctx.steering(): pin-resolved steering briefs on the branch path.
(The steering insert endpoint and projections land with the
steering/world-state spec; this file covers the read path only.)"""
import pytest

from core import EventStore
from server.context import StoreBranchContext


@pytest.fixture
def store():
    s = EventStore()
    s.append_node(None, "you", "The door was open.", node_id="A")
    s.append_node("A", "you", "Tone: gothic, quiet, exact.",
                  node_id="S", scope="workspace")
    s.set_meta("S", "kind", "steering")
    s.append_node("S", "character", "She stepped inside.", node_id="B")
    return s


def test_steering_returns_briefs_root_to_leaf(store):
    briefs = StoreBranchContext(store, "B").steering()
    assert [m.node for m in briefs] == ["S"]
    assert briefs[0].content == "Tone: gothic, quiet, exact."


def test_steering_empty_when_absent(store):
    assert StoreBranchContext(store, "A").steering() == []


def test_steering_briefs_stay_out_of_story_materialization(store):
    msgs = store.materialize("B")
    assert [m.content for m in msgs] == [
        "The door was open.", "She stepped inside."]


def test_steering_sees_revised_version(store):
    store.revise("S", "Tone: bright, wry, fast.")
    briefs = StoreBranchContext(store, "B").steering()
    assert briefs[0].content == "Tone: bright, wry, fast."
    assert briefs[0].version == 2


def test_steering_respects_pins(store):
    store.revise("S", "Tone: bright, wry, fast.")
    store.pin("S", "B", 1)
    briefs = StoreBranchContext(store, "B").steering()
    assert briefs[0].content == "Tone: gothic, quiet, exact."
    assert briefs[0].pinned
