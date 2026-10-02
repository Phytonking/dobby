# Testing Strategy

Dobby has a three-layer test suite: unit tests (mocked), integration tests (real Postgres), and Docker-sandboxed tests. Each layer validates different concerns without creating dependencies or network calls unless necessary.

## Quick start

```bash
make venv               # one-time: install all dev deps
make test               # unit suites (~1s): bot + dashboard
make test-integration   # integration suite with throwaway Postgres
make test-docker        # sandboxed suite inside shipped image
make check              # lint + unit + integration + live API (what CI runs)
```

## Layer 1: Unit tests (mocked)

**Runtime:** ~1 second. **Network:** none. **External state:** none.

All external API calls (Modal, Composio, Google Calendar, Postgres) are stubbed with canned responses. Tests run in `tests/` and `dashboard/tests/`.

### Bot unit suite (`tests/`)

Located in `tests/test_*.py` plus test subdirectories.

| Module | Covers |
| --- | --- |
| `test_agent.py` | Agent initialization, model routing, reasoning effort, tool dispatch, conversation cleanup, cost tracking |
| `test_bot.py` | Authorization checks, channel/role/user restrictions, message parsing, mention context gathering, slash command parsing |
| `test_tools.py` | Tool definition shape, argument validation, Composio schema sync (mocked API) |
| `test_contacts.py` | Contact memory: add, list, remove, JSON round-trips |
| `test_voice.py` | Voice phrasings loaded and shuffled, no hardcoded strings |
| `test_memory.py` | In-memory state: open questions, conversation cleanup, per-user cooldowns |

Run individually:
```bash
make test-bot
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_bot.py::test_authorization -v
```

### Dashboard unit suite (`dashboard/tests/`)

Covers auth routes, integration routes, local auth endpoints. Mocks database interactions.

```bash
make test-dashboard
python -m pytest dashboard/tests -v
```

## Layer 2: Integration tests (real Postgres)

**Runtime:** ~5–10 seconds. **Network:** none (Postgres runs in Docker). **External state:** throwaway database.

Tests in `tests/integration/` exercise the database schema, ORM models, and migrations. A temporary Postgres container (tmpfs, cleaned up after) runs against real SQL, JSONB/ARRAY/UUID round-trips, and schema constraints.

### When to run

- Before pushing: `make check` includes integration tests.
- After changing `dashboard/models.py` or creating migrations: integration tests validate that the model and migration are in sync.

### What it does

1. Starts a temporary Postgres container on `127.0.0.1:5433`.
2. Runs Alembic migrations to `head`.
3. Compares the migrated schema against `dashboard/models.py` with `sqlalchemy-diff`.
4. Runs test cases.
5. Cleans up.

### Debugging

To keep Postgres running between test runs:
```bash
make chat-db    # starts postgres, prints connection string
# Run tests repeatedly in another terminal
TEST_DATABASE_URL=postgresql+asyncpg://dobby:dobby@127.0.0.1:5433/dobby_test make test-integration
# Clean up when done
docker compose -f compose.test.yaml rm -sf postgres-test
```

## Layer 3: Docker-sandboxed suite

**Runtime:** ~2–3 seconds. **Network:** disabled. **External state:** none.

The suite runs inside the shipped `linux/amd64` Python 3.12 image in a read-only filesystem with no network access. This catches environment-specific failures (missing dependencies, import order issues, OS differences).

```bash
make test-docker
```

Runs both `docker compose -f compose.test.yaml run --rm tests` and lint checks.

## Layer 4: Live API validation (optional)

**When:** Only when `COMPOSIO_API_KEY` is in `.env`; otherwise skipped.

Validates that live Composio API tool schemas match `bot/tools.py` definitions. Prevents silent schema drift.

```bash
make test-composio
```

## Coverage and assertions

Dobby's test suite focuses on:

- **Authorization:** role/user/channel allowlists, permission re-checks at confirmation
- **State machines:** confirmation expiry, view consumption, duplicate clicks
- **Data integrity:** timezone handling, duration preservation, event conflict detection
- **Privacy:** mention context scope (same channel only), no DM leaks, no credential logging
- **Resilience:** stale event detection (ETags), network error handling, cooldowns

Tests do not cover:

- End-to-end Discord/Google Calendar/Modal workflows (use live smoke testing in DEPLOYMENT.md)
- UI rendering (manual testing required)
- Performance profiling

## Running a specific test

```bash
# Single test
python -m pytest tests/test_agent.py::test_agent_with_tool_calls -v

# Tests matching a pattern
python -m pytest tests -k "contact" -v

# Verbose output with print statements
python -m pytest tests/test_bot.py::test_authorization -vv -s

# Stop after first failure
python -m pytest tests -x
```

## Continuous Integration

`.github/workflows/ci.yml` runs on every push:

1. Validate all Compose files (syntax check).
2. Build the runtime image (both `linux/amd64` and `linux/arm64`).
3. Run the full suite inside `compose.test.yaml` with networking disabled.
4. On success, publish multi-architecture images to GitHub Container Registry.

PR checks cannot write to the registry; they only verify the build succeeds.

## Test fixtures and mocks

Tests use `pytest` fixtures defined in `tests/conftest.py` and `dashboard/tests/conftest.py`:

| Fixture | Provides |
| --- | --- |
| `stub_composio_session` | Stub Composio with canned tool results |
| `test_client` | FastAPI TestClient for dashboard routes |
| `db_session` | Async SQLAlchemy session (integration tests only) |

## Adding a new test

1. Create `tests/test_<feature>.py` or add to an existing file.
2. Import fixtures from `conftest.py`.
3. Mock external calls:
   ```python
   from unittest.mock import patch, AsyncMock
   
   def test_feature(stub_composio_session):
       # stub_composio_session already configured
       result = call_code_under_test(stub_composio_session)
       assert result == expected
   ```
4. Run with `python -m pytest tests/test_<feature>.py::test_feature -v`.
5. Commit: include test file in the PR to validate new behavior.

## Common issues

| Issue | Solution |
| --- | --- |
| Tests pass locally, fail in Docker | Check Python version (shipped image: 3.12; dev hosts: 3.14). Ruff `target-version` is `py312` to catch 3.14-only syntax. |
| Postgres connection refused | Start with `make chat-db` or verify `compose.test.yaml` is in the repo root. |
| Flaky test timing out | Reduce async waits in test setup; use `asyncio.timeout()` instead of hardcoded sleeps. |
| Mock not working | Ensure patch path matches the import in the module under test, not the import origin. |

