"""Dev/preview entry point: `python -m server [log.db]`.

Serves forklore with a deterministic FakeModel so the app can be explored
without any LLM credentials. Pass a path to persist the log. If
FORKLORE_DIST points at a built web client (dist/), it is served at / —
the API routes take precedence since they were registered first.
"""
import os
import sys

import uvicorn

from core import EventStore
from strategies.best_of_n import BestOfN
from strategies.models import DemoModel, FakeModel
from strategies.simple_passthrough import SimplePassthrough

from .app import create_app
from .demo import seed_demo
from .registry import StrategyRegistry

store = EventStore(sys.argv[1] if len(sys.argv) > 1 else None)
seed_demo(store)

registry = StrategyRegistry()
registry.register(SimplePassthrough(FakeModel(
    reply="The thread of the story picks itself up and carries on — "
          "\"{last}\" lingered in the air a moment longer than it should have.")))
registry.register(BestOfN(DemoModel()))

app = create_app(store, registry)

_dist = os.environ.get("FORKLORE_DIST")
if _dist and os.path.isdir(_dist):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_dist, html=True), name="spa")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1",
                port=int(os.environ.get("PORT", "8000")))
