"""Strategy registry: id -> strategy instance. The id is recorded on every
generated node (GenerationMeta.strategy), so ids must be stable and
versioned, e.g. "simple_passthrough/0.1"."""
from __future__ import annotations


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, object] = {}

    def register(self, strategy) -> None:
        self._strategies[strategy.id] = strategy

    def get(self, strategy_id: str):
        if strategy_id not in self._strategies:
            raise KeyError(f"unknown strategy {strategy_id!r}")
        return self._strategies[strategy_id]

    def list(self) -> list[str]:
        return sorted(self._strategies)
