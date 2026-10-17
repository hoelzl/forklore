# Forklore — agent guide

Forklore is a workbench for branching, LLM-assisted storytelling: a tree of
nodes with versioned content over an append-only SQLite event log, a FastAPI
server with a pluggable generation-strategy layer, and a React/TypeScript
client.

This file is the canonical instruction source for coding agents (Claude Code,
Hermes Agent, OpenCode, Kimi Code, and others). Claude Code reads it via the
`@AGENTS.md` import in `CLAUDE.md`; the rest read it natively.

## Read first

- `docs/architecture.md` — **the load-bearing invariants**. Anything touching
  `core/`, the commit path, or the strategy contract must respect it. Its
  "Do-not-regress notes" list bugs already fixed once; do not reintroduce
  them.
- `README.md` — the model, the strategy list, the API sketch.

## Commands

```bash
# Backend (Python >= 3.12); use a venv — this machine's pip refuses global installs
python -m venv .venv                      # once; .venv/ is gitignored
# Windows Git Bash: source .venv/Scripts/activate — Unix: source .venv/bin/activate
pip install -r requirements.txt && pip install pytest
python -m pytest                          # full suite (96 tests), must stay green
python -m pytest tests/test_core.py       # the executable spec of event semantics
python -m server [log.db]                 # API on :8000, seeds a demo story

# Frontend (Node)
cd web && npm install
npm run dev                               # client on :3000, proxies to :8000
npm run build                             # tsc -b && vite build — must pass
npm run lint                              # eslint
```

The app must work fully offline with zero configuration (built-in demo
model). Never make a real LLM key or a heavy dependency required to run or
test.

## Hard rules

1. **The log is the truth.** State is a pure fold over an append-only event
   log. Never write state directly; never delete — undo is compensating
   events, deletion is tombstone.
2. **One request = one transaction = one undo unit.** Multi-node operations
   commit via a single `store.commit(...)`; don't loop convenience methods.
3. **The server owns semantics.** Clients post intents and render
   projections; never reimplement fold/materialize in `web/` — add a server
   endpoint instead.
4. **`core/` stays pure.** Python + pydantic only, zero LLM dependencies.
   Per-strategy heavy deps (langgraph, litellm) import lazily inside
   `generate()` and degrade gracefully (`find_spec` guard,
   `pytest.importorskip` in tests).
5. **GenerationMeta is the reproducibility contract.** Every generated node
   carries `{strategy, model, params, watermark, context_hash}`; always
   `ctx.record_prompt(...)` with the exact prompt messages.

## Conventions

- Backend errors: raise `KeyError` (→404) / `InvariantError` (→409) from
  store-touching code; `guard()` maps them. Don't raise `HTTPException`.
- Strategies: read through `BranchContext`, declare results with
  `ctx.plan_node(...)`, stream by yielding tokens. New meta-role voices must
  be added to `LiteLLMModel.role_of` AND the `DemoModel` reply cycle.
- Tests: `ScriptedModel` scripts are exactly as long as the expected model
  call count — exhaustion is a feature. When you change semantics, change
  the tests deliberately, not accidentally.
- Frontend: Zustand store is a thin projection; every mutation goes through
  the API then `refresh()`.
- Python ≥ 3.12, pydantic v2; React 19, TypeScript, Vite, Tailwind,
  shadcn-style Radix components.

## Layout

```
core/        event schema, fold, materialize, undo (pure, no LLM deps)
server/      FastAPI + SSE: commit routes, projections, registry, settings
strategies/  strategy implementations + model clients (Fake/Scripted/Demo/LiteLLM)
web/         React + TS + Vite client
tests/       pytest suite encoding the semantics
docs/        architecture invariants and the strategy contract
```

## Notes

- `scripts/sync_preview.sh` / `export_update.sh` are legacy sandbox tooling —
  harmless, ignore them.
- `*.db` files are local event logs, gitignored; don't commit them.
