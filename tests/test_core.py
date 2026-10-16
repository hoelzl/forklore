"""Semantics of the forklore core, encoded as tests.

These tests are the contract: revise propagates, variant+graft diverges,
nothing is ever deleted, undo is a compensating append, pins freeze one
branch, watermarks reconstruct the past.
"""
import pytest

from core import (
    EventStore, GenerationMeta, InvariantError, hash_messages, fold,
)


@pytest.fixture
def store():
    return EventStore()  # in-memory


@pytest.fixture
def branched(store):
    """A -> B -> C1 -> D1
               \-> C2"""
    store.append_node(None, "user", "The door was open.", node_id="A")
    store.append_node("A", "char", "She stepped inside.", node_id="B")
    store.append_node("B", "char", "Branch one.", node_id="C1")
    store.append_node("B", "char", "Branch two.", node_id="C2")
    store.append_node("C1", "char", "Branch one continues.", node_id="D1")
    return store


def texts(msgs):
    return [m.content for m in msgs]


# -- basics ---------------------------------------------------------------

def test_linear_materialize(store):
    store.append_node(None, "user", "Hello.", node_id="A")
    store.append_node("A", "char", "Hi there.", node_id="B")
    msgs = store.materialize("B")
    assert texts(msgs) == ["Hello.", "Hi there."]
    assert [m.node for m in msgs] == ["A", "B"]
    assert all(m.version == 1 and not m.pinned for m in msgs)


def test_single_root_enforced(store):
    store.append_node(None, "user", "root", node_id="A")
    with pytest.raises(InvariantError):
        store.append_node(None, "user", "second root")


def test_node_ids_never_reused(store):
    store.append_node(None, "user", "root", node_id="A")
    with pytest.raises(InvariantError):
        store.append_node("A", "user", "dupe", node_id="A")


# -- revise: the typo fix --------------------------------------------------

def test_revise_propagates_to_all_branches(branched):
    branched.revise("A", "The door was ajar.")
    assert texts(branched.materialize("D1"))[0] == "The door was ajar."
    assert texts(branched.materialize("C2"))[0] == "The door was ajar."


def test_revise_keeps_old_version(branched):
    ev = branched.revise("A", "The door was ajar.")
    node = branched.state().nodes["A"]
    assert node.version == 2
    assert node.versions[0] == "The door was open."
    # time travel: as-of just before the revise, the old text is visible
    assert texts(branched.materialize("D1", at=ev.seq - 1))[0] \
        == "The door was open."


def test_watermark_excludes_future_nodes(branched):
    s = branched.state(at=3)  # A, B, C1 exist; C2, D1 do not
    assert set(s.nodes) == {"A", "B", "C1"}


# -- variant + graft: the storyline divergence -----------------------------

def test_diverge_moves_only_one_subtree(branched):
    v, g = branched.diverge("B", "She stepped back.", move="C2",
                            variant_id="B2")
    assert texts(branched.materialize("C2"))[1] == "She stepped back."
    assert texts(branched.materialize("D1"))[1] == "She stepped inside."
    assert branched.state().nodes["B"].children == ["C1"]
    assert branched.state().nodes["B2"].children == ["C2"]
    # same tx = one undo unit
    assert v.tx == g.tx


def test_undo_diverge_restores_structure(branched):
    v, _ = branched.diverge("B", "She stepped back.", move="C2",
                            variant_id="B2")
    head_before = branched.head
    branched.undo(v.tx)
    assert branched.state().nodes["B"].children == ["C1", "C2"]
    assert branched.state().nodes["B2"].tombstoned
    assert texts(branched.materialize("C2"))[1] == "She stepped inside."
    # nothing was removed: the log only grew
    assert branched.head == head_before + 2
    # ...and the divergent world is still perfectly reconstructible
    assert texts(branched.materialize("C2", at=head_before))[1] \
        == "She stepped back."


# -- tombstone / restore ----------------------------------------------------

def test_tombstone_hides_but_never_erases(branched):
    branched.tombstone("C2")
    assert branched.state().is_hidden("C2")
    assert branched.state().visible_children("B") == ["C1"]
    # tombstone affects discoverability, not materialization
    assert texts(branched.materialize("C2"))[-1] == "Branch two."
    branched.restore("C2")
    assert not branched.state().is_hidden("C2")
    assert branched.state().visible_children("B") == ["C1", "C2"]


def test_append_under_tombstone_rejected(branched):
    branched.tombstone("C2")
    with pytest.raises(InvariantError):
        branched.append_node("C2", "char", "sneaky")


# -- pins -------------------------------------------------------------------

def test_pin_freezes_one_branch(store):
    store.append_node(None, "user", "He was tired.", node_id="A")
    store.append_node("A", "char", "Path via B.", node_id="B")
    store.append_node("A", "char", "Path via E.", node_id="E")
    store.revise("A", "He was exhausted.")
    store.pin("A", via_child="B", version=1)
    assert texts(store.materialize("B"))[0] == "He was tired."
    assert texts(store.materialize("E"))[0] == "He was exhausted."
    m = store.materialize("B")[0]
    assert m.pinned and m.version == 1


def test_pin_requires_existing_edge(store):
    store.append_node(None, "user", "root", node_id="A")
    store.append_node("A", "char", "child", node_id="B")
    store.append_node("B", "char", "grandchild", node_id="C")
    with pytest.raises(InvariantError):
        store.pin("A", via_child="C", version=1)  # C is not a child of A


def test_dangling_pin_is_inert(store):
    store.append_node(None, "user", "v1 root", node_id="A")
    store.append_node("A", "char", "via B", node_id="B")
    store.append_node("A", "char", "via E", node_id="E")
    store.pin("A", via_child="B", version=1)
    store.graft("B", "E")  # destroys the (A, B) edge the pin sat on
    store.revise("A", "v2 root")
    assert texts(store.materialize("B"))[0] == "v2 root"  # pin inert


# -- stale detection ---------------------------------------------------------

def test_stale_after_upstream_revise(branched):
    assert branched.stale("D1") == []
    branched.revise("B", "She stepped inside, quietly.")
    assert branched.stale("C1") == ["B"]
    assert branched.stale("D1") == ["B"]
    # nodes written *after* the revise are not stale
    branched.append_node("C2", "char", "Fresh.", node_id="F")
    assert branched.stale("F") == []


def test_pinned_branch_is_frozen_not_stale(store):
    store.append_node(None, "user", "root v1", node_id="A")
    store.append_node("A", "char", "via B", node_id="B")
    store.append_node("A", "char", "via E", node_id="E")
    store.revise("A", "root v2")
    store.pin("A", via_child="B", version=1)
    assert store.stale("B") == []
    assert store.stale("E") == ["A"]


# -- undo --------------------------------------------------------------------

def test_undo_revise_restores_content(branched):
    ev = branched.revise("A", "The door was ajar.")
    branched.undo(ev.tx)
    node = branched.state().nodes["A"]
    assert node.content == "The door was open."
    # undo is accretive: the restore is itself a new version
    assert node.versions == [
        "The door was open.", "The door was ajar.", "The door was open."]


def test_redo_is_undo_of_the_undo_tx(branched):
    ev = branched.revise("A", "The door was ajar.")
    undid = branched.undo(ev.tx)
    branched.undo(undid[0].tx)  # redo
    assert branched.state().nodes["A"].content == "The door was ajar."


def test_undo_tombstone_restores(branched):
    ev = branched.tombstone("C2")
    branched.undo(ev.tx)
    assert not branched.state().nodes["C2"].tombstoned


def test_compensating_events_carry_undoes_marker(branched):
    ev = branched.revise("A", "ajar")
    undid = branched.undo(ev.tx)
    assert undid[0].undoes == ev.tx


# -- graft safety -------------------------------------------------------------

def test_graft_cycle_rejected(branched):
    with pytest.raises(InvariantError):
        branched.graft("A", "D1")  # root under its own descendant
    with pytest.raises(InvariantError):
        branched.graft("B", "C2")  # B under its own child
    with pytest.raises(InvariantError):
        branched.graft("A", "B")   # the root, period
    # failed commits write nothing
    assert set(branched.state().nodes) == {"A", "B", "C1", "C2", "D1"}


def test_graft_is_one_pointer(branched):
    branched.graft("C1", "C2")  # move the whole C1 subtree (incl. D1)
    assert texts(branched.materialize("D1")) == [
        "The door was open.", "She stepped inside.",
        "Branch two.", "Branch one.", "Branch one continues."]


# -- workspace scope -----------------------------------------------------------

def test_workspace_nodes_hidden_from_story_materialization(branched):
    branched.append_node("B", "strategy:critic", "note: too slow",
                         node_id="W", scope="workspace")
    assert texts(branched.materialize("C1")) == \
        ["The door was open.", "She stepped inside.", "Branch one."]
    msgs = branched.materialize("W", include_workspace=True)
    assert msgs[-1].scope == "workspace"
    assert texts(store_msgs := branched.materialize("W")) == \
        ["The door was open.", "She stepped inside."]


# -- generation meta / reproducibility -----------------------------------------

def test_generation_meta_records_watermark_and_hash(branched):
    ctx = branched.materialize("C1")
    wm = branched.head
    meta = GenerationMeta(
        strategy="simple_passthrough/0.1", model="test-model",
        watermark=wm, context_hash=hash_messages(ctx))
    branched.append_node("C1", "char", "Generated reply.",
                         node_id="G", meta=meta, actor="strategy:test")
    node = branched.state().nodes["G"]
    assert node.generation is not None
    assert node.generation.watermark == wm
    # the exact context the model saw is reconstructible forever
    assert hash_messages(branched.materialize("C1", at=wm)) == \
        node.generation.context_hash


# -- persistence -----------------------------------------------------------------

def test_log_survives_reopen(branched, tmp_path):
    db = str(tmp_path / "log.db")
    s1 = EventStore(db)
    s1.append_node(None, "user", "root", node_id="A")
    s1.append_node("A", "char", "child", node_id="B")
    s1.revise("A", "root v2")
    s2 = EventStore(db)  # reopen: replay the log
    assert s2.head == 3
    assert texts(s2.materialize("B")) == ["root v2", "child"]
    # at seq 2 (B created, A not yet revised) the past is reconstructed
    assert texts(s2.materialize("B", at=2)) == ["root", "child"]


def test_failed_commit_leaves_log_untouched(branched):
    head = branched.head
    with pytest.raises(InvariantError):
        branched.commit([
            # second event is invalid; neither must be written
            __import__("core").Append(payload=__import__("core").events.AppendPayload(
                node="X", parent="B", author="char", content="ok")),
            __import__("core").Graft(payload=__import__("core").events.GraftPayload(
                subtree="A", new_parent="X")),
        ])
    assert branched.head == head
    assert "X" not in branched.state().nodes


# -- the worked trace from the design discussion ---------------------------------

def test_worked_trace(store):
    """The seq 1..8 scenario from the spec conversation."""
    store.append_node(None, "user", "The door was open.", node_id="A")
    store.append_node("A", "char", "She stepped inside.", node_id="B")
    store.append_node("B", "char", "b1", node_id="C1")
    store.append_node("B", "char", "b2", node_id="C2")
    store.revise("A", "The door was ajar.")                      # seq 5
    store.append_node("C1", "char", "b1 continues", node_id="D1")
    v, _ = store.diverge("B", "She stepped back.", move="C2",
                         variant_id="B2")                        # seq 7+8
    assert texts(store.materialize("C2")) == \
        ["The door was ajar.", "She stepped back.", "b2"]
    assert texts(store.materialize("D1"))[1] == "She stepped inside."
    assert store.stale("C1") == ["A"]
    store.undo(v.tx)                                             # seq 9+10
    assert texts(store.materialize("C2"))[1] == "She stepped inside."
    assert store.head == 10  # 8 events + 2 compensations; nothing removed
