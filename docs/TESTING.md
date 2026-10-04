# Testing

Each layer catches a different class of bug; `make help` lists every target.

| Layer | Where | Needs | Catches |
|---|---|---|---|
| Bot unit | `tests/` (incl. `tests/integrations/<service>/`) | nothing | agent loop, provider adapters and failover (`test_ai_models.py`), Composio schema → declaration bridge (`test_composio.py`), Discord client/commands/confirmations, each integration's tools |
| Dashboard | `dashboard/tests/` | nothing | auth and sessions, OAuth and local login, admin gating, service-account routes, `LOCAL_MODE` |
| Postgres | `tests/integration/` | Docker | migrations apply, ORM-vs-schema drift, `bot/memory.py` raw SQL (upserts, timestamp guards), late-invitation approvals (atomic claims, the per-event lock, retry timestamps), dashboard model constraints, the agent loop end to end against a real database |
| Live Composio | `tests/live/` | `COMPOSIO_API_KEY` | a curated action renamed upstream or a stale `TOOLKIT_VERSIONS` pin |
| Docker | `compose.test.yaml` | Docker | the unit suite and lint inside the shipped Python 3.12 image, read-only, no network |
| System regression | `test_regression/` | Docker | built images, real Postgres, bot smoke, dashboard and frontend in a browser ([README](../test_regression/README.md)) |
| Behavioral evals | `scripts/eval.py` | AI_* settings, `COMPOSIO_API_KEY` | what the live model actually does with the production tools ([EVALS.md](EVALS.md)) |

## Running

```bash
make check              # lint + bot + dashboard + Postgres + live Composio (what CI runs, minus Docker)
make test               # bot + dashboard unit suites only, ~2s, offline
make test-integration   # Postgres suite on a throwaway container (tmpfs, removed afterwards)
make test-composio      # live Composio check; skipped cleanly without a key
make test-docker        # unit suite + lint in the shipped image
make test-regression    # upstream's system suite
make eval / chat        # live model; see EVALS.md and scripts/chat.py
```

Single test: `.venv/bin/python -m pytest tests/test_agent.py -k fallback -v`.

## Conventions

- **Opt-in for anything external.** `tests/integration/` skips unless `TEST_DATABASE_URL` is set;
  `tests/live/` skips unless `COMPOSIO_API_KEY` is a real environment variable. Neither reads
  `.env` on its own, so a plain `pytest -q` never touches a network or a database. The Make
  targets set them for one invocation.
- **Python 3.12 is the target.** The image and CI run 3.12. A newer host Python works, and ruff's
  `target-version = "py312"` rejects syntax the image can't run (e.g. 3.14's unparenthesised
  `except A, B:`).
- **Fakes sit at one boundary each.** Model calls go through `httpx` (patch
  `bot.AIModels.httpx.AsyncClient` with a `MockTransport`) or the Gemini client; Composio execution
  goes through `bot.composio.execute_tool`. Tests above those boundaries run production code.

## CI

`.github/workflows/ci.yml` runs on every pull request and push to `main`: secret guard, ruff,
the bot and dashboard suites, the Postgres suite against a service container, compose validation,
a Docker build with an import smoke test, the in-image suite, and upstream's regression workflow.
`publish.yml` only runs after all of that passes on `main`.
