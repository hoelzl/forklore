"""beat_planner: plan-then-execute with the roadmap as a tree citizen —
first call plans and drafts beat 1, follow-ups reuse the roadmap, an
exhausted or undone plan triggers a fresh outline."""
import pytest
from fastapi.testclient import TestClient

from core import EventStore
from strategies.beat_planner import BeatPlanner, parse_beats
from strategies.models import ScriptedModel
from server import StrategyRegistry, create_app

from test_strategies import parse_sse

SHEET = "1. Find the door.\n2. Open the door.\n3. Regret the door."
SHEET2 = "1. Close the door."


def make_client(script):
    store = EventStore()
    store.append_node(None, "you", "The house was quiet.", node_id="A")
    store.append_node("A", "character", "Too quiet.", node_id="B")
    registry = StrategyRegistry()
    registry.register(BeatPlanner(ScriptedModel(script)))
    return TestClient(create_app(store, registry))


def generate(client, leaf, params=None):
    r = client.post("/generate", json={
        "leaf": leaf, "strategy": "beat_planner/0.1",
        "author": "character", "params": params or {}})
    assert r.status_code == 200
    events = parse_sse(r.text)
    committed = next(d for k, d in events if k == "committed")
    story = [e for e in committed["events"]
             if e["payload"]["scope"] == "story"]
    ws = [e for e in committed["events"]
          if e["payload"]["scope"] == "workspace"]
    tokens = "".join(d["delta"] for k, d in events if k == "token")
    return committed, story, ws, tokens


# -- parsing ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1. Find the door.\n2. Open it.", ["Find the door.", "Open it."]),
    ("1) Find.\n2) Open.", ["Find.", "Open."]),
    ("  1. Indented.\n2. List.", ["Indented.", "List."]),
    ("- Find.\n- Open.", ["Find.", "Open."]),          # bullets fallback
    ("Find.\nOpen.", ["Find.", "Open."]),               # bare lines
    ("Just one beat.", ["Just one beat."]),             # single line
    ("1. Find.\nSome stray note.", ["Find."]),          # numbered wins
])
def test_parse_beats(text, expected):
    assert parse_beats(text) == expected


# -- plan-then-execute --------------------------------------------------------

def test_first_generate_plans_then_drafts_one_beat():
    client = make_client([SHEET, "Mara found the door."])
    committed, story, ws, tokens = generate(client, "B")

    assert [e["payload"]["content"] for e in story] == ["Mara found the door."]
    assert tokens == "Mara found the door."
    assert len(ws) == 1                                  # the roadmap
    plan = ws[0]["payload"]["meta"]["params"]
    assert plan["stage"] == "plan"
    assert plan["beats"] == ["Find the door.", "Open the door.",
                             "Regret the door."]
    meta = story[0]["payload"]["meta"]["params"]
    assert (meta["beat"], meta["of"]) == (1, 3)
    assert meta["plan_id"] == plan["plan_id"]
    assert meta["beat_text"] == "Find the door."
    # plan + beat committed atomically
    assert {e["tx"] for e in committed["events"]} == {committed["events"][0]["tx"]}


def test_followup_reuses_roadmap_without_replanning():
    # exactly three script entries: a re-plan would exhaust the script
    client = make_client([SHEET, "Mara found the door.", "She opened it."])
    _, story1, _, _ = generate(client, "B")
    leaf1 = story1[0]["payload"]["node"]
    _, story2, ws2, _ = generate(client, leaf1)

    meta2 = story2[0]["payload"]["meta"]["params"]
    assert story2[0]["payload"]["content"] == "She opened it."
    assert (meta2["beat"], meta2["of"]) == (2, 3)
    assert meta2["plan_id"] == story1[0]["payload"]["meta"]["params"]["plan_id"]
    assert ws2 == []                                     # no new plan node


def test_exhausted_roadmap_triggers_replan():
    client = make_client([SHEET2, "Mara closed it.", SHEET2, "She locked it."])
    _, story1, _, _ = generate(client, "B", params={"beats": 1})
    _, story2, ws2, _ = generate(client, story1[0]["payload"]["node"],
                                 params={"beats": 1})
    meta1 = story1[0]["payload"]["meta"]["params"]
    meta2 = story2[0]["payload"]["meta"]["params"]
    assert (meta1["beat"], meta1["of"]) == (1, 1)
    assert (meta2["beat"], meta2["of"]) == (1, 1)        # fresh roadmap
    assert meta2["plan_id"] != meta1["plan_id"]
    assert len(ws2) == 1


def test_replan_param_forces_fresh_roadmap():
    client = make_client([SHEET, "Draft one.", SHEET2, "Draft two."])
    _, story1, _, _ = generate(client, "B")
    _, story2, ws2, _ = generate(client, story1[0]["payload"]["node"],
                                 params={"replan": True})
    assert story2[0]["payload"]["meta"]["params"]["plan_id"] != \
        story1[0]["payload"]["meta"]["params"]["plan_id"]
    assert len(ws2) == 1


def test_undone_beat_is_offered_again():
    # plan+beat1 commit as tx1, beat2 as tx2; undoing tx2 leaves the
    # roadmap alive, so the planner offers beat 2 again
    client = make_client([SHEET, "Draft one.", "Draft two.",
                          "Draft two, take two."])
    _, story1, _, _ = generate(client, "B")
    leaf1 = story1[0]["payload"]["node"]
    committed2, story2, _, _ = generate(client, leaf1)
    assert story2[0]["payload"]["meta"]["params"]["beat"] == 2
    client.post(f"/undo/{committed2['events'][0]['tx']}")   # undo beat 2 only
    _, story3, ws3, _ = generate(client, leaf1)
    assert story3[0]["payload"]["meta"]["params"]["beat"] == 2
    assert story3[0]["payload"]["content"] == "Draft two, take two."
    assert story3[0]["payload"]["meta"]["params"]["plan_id"] == \
        story1[0]["payload"]["meta"]["params"]["plan_id"]
    assert ws3 == []                                        # no re-plan


def test_undone_plan_triggers_replan():
    # undoing the plan+beat tx tombstones the roadmap itself
    client = make_client([SHEET, "Draft one.", SHEET2, "Draft two."])
    committed, _, _, _ = generate(client, "B")
    client.post(f"/undo/{committed['events'][0]['tx']}")
    _, story2, ws2, _ = generate(client, "B")
    assert story2[0]["payload"]["meta"]["params"]["beat"] == 1
    assert len(ws2) == 1   # a fresh roadmap was planned


def test_beat_two_workshop_shows_the_roadmap():
    client = make_client([SHEET, "Draft one.", "Draft two."])
    _, story1, _, _ = generate(client, "B")
    _, story2, _, _ = generate(client, story1[0]["payload"]["node"])
    arts = client.get(
        f"/workspace/{story2[0]['payload']['node']}").json()["artifacts"]
    assert len(arts) == 1
    assert arts[0]["generation"]["params"]["stage"] == "plan"
    assert arts[0]["generation"]["params"]["beats"][0] == "Find the door."
