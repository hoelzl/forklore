"""Demo seed: a small pre-branched story so a fresh server (or preview)
opens onto something that shows what forklore is for."""
from __future__ import annotations

from core.store import EventStore

OPENING = "The lighthouse had been dark for eleven years when Mara came to relight it."

M1 = ("The door was not locked. The keeper's log said it never had been — "
      "a lighthouse wants to be entered.")

BRANCH_A1 = ("Inside, the stairs spiraled up into salt-dark. Somewhere above, "
             "something turned over in its sleep.")
BRANCH_A2 = ("Mara climbed. At the seventh turn she found a note pinned with a "
             "rusted nail: DO NOT LIGHT IT IF THE FOG SINGS.")

BRANCH_B1 = ("Inside, the lamp room was already lit — low and blue, though no "
             "oil had been delivered in a decade.")
BRANCH_B2 = ("Mara set down her matches. \"Then what was I sent to light?\" "
             "she asked the blue flame.")


def seed_demo(store: EventStore) -> None:
    if store.state().root is not None:
        return
    store.append_node(None, "you", OPENING)
    root = store.state().root
    assert root is not None
    store.append_node(root, "character", M1)
    (m1,) = [c for c in store.state().nodes[root].children]
    a = store.append_node(m1, "character", BRANCH_A1)
    store.append_node(a.payload.node, "character", BRANCH_A2)
    b = store.append_node(m1, "character", BRANCH_B1)
    store.append_node(b.payload.node, "you", BRANCH_B2)
    store.set_meta(m1, "note", "the fork: two readings of the lighthouse")
