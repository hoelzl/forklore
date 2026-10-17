"""Settings: provider inference, redaction, persistence, per-request model
resolution, and the full /generate path against a fake litellm module."""
import importlib.machinery
import json
import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from core import EventStore
from strategies.models import FakeModel
from strategies.simple_passthrough import SimplePassthrough
from server import StrategyRegistry, create_app
from server.settings import SettingsStore, provider_of

from test_server import parse_sse


# -- provider inference --------------------------------------------------------

@pytest.mark.parametrize("model,provider", [
    ("anthropic/claude-sonnet-4-5", "anthropic"),
    ("openrouter/auto", "openrouter"),
    ("ollama/llama3.1", "ollama"),
    ("gpt-4o", "openai"),
    ("o3-mini", "openai"),
    ("claude-opus-4-1", "anthropic"),
    ("mistral-large", None),
])
def test_provider_of(model, provider):
    assert provider_of(model) == provider


# -- store semantics -------------------------------------------------------------

def test_update_redacts_and_deletes(tmp_path):
    s = SettingsStore(str(tmp_path / "s.json"))
    out = s.update({"default_model": "gpt-4o",
                    "api_keys": {"openai": "sk-secret-key-1234"}})
    assert out["api_keys"]["openai"] == "…1234"      # redacted on the wire
    assert s.get()["api_keys"]["openai"] == "sk-secret-key-1234"  # not inside
    # empty string deletes a key
    s.update({"api_keys": {"openai": ""}})
    assert "openai" not in s.get()["api_keys"]


def test_persists_across_instances(tmp_path):
    p = str(tmp_path / "s.json")
    SettingsStore(p).update({"default_model": "claude-opus-4-1",
                             "strategy_models": {"best_of_n/0.1": "gpt-4o"}})
    s2 = SettingsStore(p)
    assert s2.get()["default_model"] == "claude-opus-4-1"
    assert s2.resolve_model("best_of_n/0.1", {}) == "gpt-4o"


def test_resolution_precedence():
    s = SettingsStore()
    s.update({"default_model": "A",
              "strategy_models": {"strat/1": "B"}})
    assert s.resolve_model("strat/1", {}) == "B"          # per-strategy wins
    assert s.resolve_model("other/1", {}) == "A"          # falls to default
    assert s.resolve_model("strat/1", {"model": "C"}) == "C"  # explicit wins
    s.update({"default_model": None})
    assert s.resolve_model("other/1", {}) is None         # cleared -> demo model


# -- HTTP endpoints ----------------------------------------------------------------

@pytest.fixture
def client():
    store = EventStore()
    registry = StrategyRegistry()
    registry.register(SimplePassthrough(FakeModel(reply="plain demo reply")))
    return TestClient(create_app(store, registry,
                                 settings=SettingsStore()))  # in-memory


def test_settings_roundtrip(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert r.json()["default_model"] is None
    r = client.put("/settings", json={
        "default_model": "gpt-4o", "api_keys": {"openai": "sk-xyz-9876"}})
    assert r.json()["api_keys"]["openai"] == "…9876"
    assert client.get("/settings").json()["default_model"] == "gpt-4o"


def test_settings_rejects_unknown_keys(client):
    assert client.put("/settings", json={"nonsense": 1}).status_code == 422


def test_missing_litellm_yields_sse_error(client):
    """Configuring a real model where litellm is absent must surface a
    readable SSE error event, not a stack trace."""
    sys.modules.pop("litellm", None)
    client.put("/settings", json={"default_model": "gpt-4o"})
    root = client.post("/nodes", json={
        "parent": None, "author": "user", "content": "Hi."}).json()
    leaf = root["payload"]["node"]
    r = client.post("/generate", json={
        "leaf": leaf, "strategy": "simple_passthrough/0.1"})
    events = parse_sse(r.text)
    errors = [d for k, d in events if k == "error"]
    assert errors and "litellm" in errors[0]["message"]


# -- full real-model path against a fake litellm --------------------------------

class _Delta:
    content = "a real model answered this"


class _Choice:
    delta = _Delta()


class _Chunk:
    choices = [_Choice()]


class _FakeResponse:
    async def __aiter__(self):
        yield _Chunk()


def _install_fake_litellm(captured: dict):
    mod = ModuleType("litellm")
    mod.__spec__ = importlib.machinery.ModuleSpec("litellm", loader=None)

    async def acompletion(model, messages, stream, **kwargs):
        captured["model"] = model
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return _FakeResponse()

    mod.acompletion = acompletion
    sys.modules["litellm"] = mod


def test_generate_uses_configured_model(client):
    captured: dict = {}
    _install_fake_litellm(captured)
    try:
        client.put("/settings", json={
            "default_model": "openai/gpt-4o",
            "api_keys": {"openai": "sk-test-4321"},
            "default_params": {"temperature": 0.4}})
        root = client.post("/nodes", json={
            "parent": None, "author": "you", "content": "Light the lamp."}).json()
        leaf = root["payload"]["node"]
        r = client.post("/generate", json={
            "leaf": leaf, "strategy": "simple_passthrough/0.1"})
        events = parse_sse(r.text)
        # tokens streamed from the "real" model, not the FakeModel reply
        tokens = "".join(d["delta"] for k, d in events if k == "token")
        assert tokens == "a real model answered this"
        # litellm received the resolved model, key, and merged params
        assert captured["model"] == "openai/gpt-4o"
        assert captured["kwargs"]["api_key"] == "sk-test-4321"
        assert captured["kwargs"]["temperature"] == 0.4
        assert captured["messages"] == [
            {"role": "user", "content": "Light the lamp."}]
        # provenance records the real model name
        committed = next(d for k, d in events if k == "committed")
        assert committed["event"]["payload"]["meta"]["model"] == "openai/gpt-4o"
    finally:
        sys.modules.pop("litellm", None)


def test_demo_model_when_nothing_configured(client):
    root = client.post("/nodes", json={
        "parent": None, "author": "user", "content": "Hi."}).json()
    r = client.post("/generate", json={
        "leaf": root["payload"]["node"],
        "strategy": "simple_passthrough/0.1"})
    tokens = "".join(d["delta"] for k, d in parse_sse(r.text) if k == "token")
    assert tokens == "plain demo reply"
