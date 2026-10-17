"""Server-side model/provider settings: the ergonomic alternative to
hand-editing env vars.

Stored as one JSON file (atomic writes, thread-safe). API keys never
leave the server: they are passed to litellm per request, and the GET
endpoint only ever returns a redacted view (last 4 characters).

Resolution order for a generation request:

  1. params["model"]           (explicit per-call override)
  2. strategy_models[strategy] (per-strategy preference)
  3. default_model             (global preference)
  4. None -> the strategy keeps whatever model it was constructed with
     (the demo app ships deterministic fake models, so it works offline)

Provider inference follows litellm's "provider/model" convention, with
bare "gpt-*"/"o1-*"/"o3-*" mapping to openai and bare "claude-*" to
anthropic. ollama and other local providers need no key.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

_KEYLESS_PROVIDERS = {"ollama", "ollama_chat", "lm_studio", "vllm",
                      "openai_like", "hosted_vllm"}

_DEFAULTS: dict[str, Any] = {
    "default_model": None,
    "image_model": None,     # litellm image model for generated-node art
    "api_keys": {},          # provider -> secret
    "strategy_models": {},   # strategy id -> litellm model string
    "default_params": {},    # temperature, max_tokens, ... merged into calls
}


class SettingsStore:
    """JSON-file-backed settings. `path=None` gives an in-memory store
    (tests, ephemeral runs)."""

    def __init__(self, path: str | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {k: (dict(v) if isinstance(v, dict) else v)
                                      for k, v in _DEFAULTS.items()}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            for k in _DEFAULTS:
                if k in saved:
                    if isinstance(_DEFAULTS[k], dict):
                        self._data[k].update(saved[k])
                    else:
                        self._data[k] = saved[k]

    # -- reads ---------------------------------------------------------------

    def get(self) -> dict[str, Any]:
        """Full settings including secrets -- server-internal use only."""
        with self._lock:
            return json.loads(json.dumps(self._data))

    def redacted(self) -> dict[str, Any]:
        """Safe for the wire: secrets reduced to a last-4 hint."""
        with self._lock:
            d = json.loads(json.dumps(self._data))
        d["api_keys"] = {p: f"…{k[-4:]}" if len(k) >= 4 else "…"
                         for p, k in d["api_keys"].items()}
        return d

    # -- writes ----------------------------------------------------------------

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Shallow-merge a patch. For the dict fields, an empty-string
        value deletes the key; `default_model=None` clears the default."""
        with self._lock:
            for key in ("api_keys", "strategy_models", "default_params"):
                if key in patch and patch[key] is not None:
                    for k, v in patch[key].items():
                        if v == "" or v is None:
                            self._data[key].pop(k, None)
                        else:
                            self._data[key][k] = v
            if "default_model" in patch:
                self._data["default_model"] = patch["default_model"] or None
            if "image_model" in patch:
                self._data["image_model"] = patch["image_model"] or None
            if self.path:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2)
                os.replace(tmp, self.path)
        return self.redacted()

    # -- model resolution --------------------------------------------------------

    def resolve_model(self, strategy_id: str,
                      params: dict[str, Any]) -> str | None:
        with self._lock:
            return (params.get("model")
                    or self._data["strategy_models"].get(strategy_id)
                    or self._data["default_model"])

    def api_key_for(self, model: str) -> str | None:
        provider = provider_of(model)
        if not provider or provider in _KEYLESS_PROVIDERS:
            return None
        with self._lock:
            return self._data["api_keys"].get(provider)

    def generation_defaults(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data["default_params"])


def provider_of(model: str) -> str | None:
    """litellm 'provider/model' prefix, with the common bare-model
    conventions resolved. None when we cannot tell (litellm will fall
    back to its own env-var handling)."""
    if "/" in model:
        return model.split("/", 1)[0].lower()
    low = model.lower()
    if low.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")):
        return "openai"
    if low.startswith("claude"):
        return "anthropic"
    return None


def build_model(model: str, settings: SettingsStore):
    """Instantiate the production client for a resolved model string.
    Raises RuntimeError with a clear message when litellm is absent --
    the route maps that to an SSE error event, not a crash."""
    try:
        from strategies.models import LiteLLMModel
        kwargs: dict[str, Any] = {}
        key = settings.api_key_for(model)
        if key:
            kwargs["api_key"] = key
        return LiteLLMModel(model, **kwargs)
    except ImportError:
        raise RuntimeError(
            f"model {model!r} requires litellm "
            f"(pip install litellm), which is not installed here")


def build_image_model(settings: SettingsStore):
    """Instantiate the configured image model, or None when unconfigured
    (strategies then keep their built-in demo image model). Raises
    RuntimeError with a clear message when litellm is absent."""
    model = settings.get()["image_model"]
    if not model:
        return None
    try:
        from strategies.images import LiteLLMImageModel
        kwargs: dict[str, Any] = {}
        key = settings.api_key_for(model)
        if key:
            kwargs["api_key"] = key
        return LiteLLMImageModel(model, **kwargs)
    except ImportError:
        raise RuntimeError(
            f"image model {model!r} requires litellm "
            f"(pip install litellm), which is not installed here")
