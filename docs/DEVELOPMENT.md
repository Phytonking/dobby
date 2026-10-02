# Development Workflow

How to set up, test, and deploy changes to Dobby.

## Prerequisites

- **Python 3.14+** (host, for local development)
- **Docker** (for Docker-sandboxed tests and running Dobby locally)
- **Git** with commit signing (optional, enforced in CI if `.github/workflows/ci.yml` is updated)

Node.js and npm are only needed if you develop the frontend dashboard.

## Initial setup

```bash
# Clone and enter the repo
git clone <repo-url>
cd dobby

# Create virtual environment and install dev dependencies
make venv

# Load .env for testing (optional; only needed if you want to run evals or chat with live keys)
cp .env.example .env
# Edit .env with your MODAL_BASE_URL, MODAL_PROXY_TOKEN_ID, etc. if testing evals
```

All other tasks run in Docker, so you don't need to install Compose files or dependencies locally.

## Development modes

### 1. Offline: write code, run tests

Fastest feedback loop. No Discord, Google, or Modal needed.

```bash
# Make a change to bot/something.py

# Run unit tests
make test

# Add a test for the new behavior (tests/test_something.py)

# Run unit + integration tests (validates Postgres schema too)
make check

# Format and lint
make fmt
```

### 2. Interactive chat: test with a real model

Test the agent pipeline without Discord setup.

```bash
# One-time: start a Postgres container
make chat-db

# Launch interactive REPL
make chat

# Type messages:
# > schedule a meeting tomorrow at 2pm
# (real Modal/Composio keys from .env; will make real tool calls)
```

To test without keys (scripted model responses, stubbed tools):

```bash
make chat-fake
# Same REPL, but uses dummy completions and tool results
```

### 3. Behavioral evals: test real model decisions

Runs scripted test cases through the live agent with stubbed tool execution.

```bash
make eval
# Output: evals/eval-<timestamp>.md with pass/fail counts
```

### 4. Docker sandbox: test inside the shipped image

Ensures everything works in the read-only Python 3.12 image that ships.

```bash
make test-docker
# Runs inside the `runtime` stage of the Dockerfile
```

## Making a change

### Code changes (not tests)

```bash
# 1. Write or modify bot/, dashboard/, or scripts/ code
# 2. Run format
make fmt

# 3. Run tests
make test

# 4. If successful, commit
git add <files>
git commit -m "Fix description"

# 5. If tests failed, fix and repeat
```

### Schema changes (database migrations)

When you change `dashboard/models.py`:

```bash
# 1. Create a new migration
alembic revision --autogenerate -m "Add column to users"
# (edit migrations/versions/<rev>.py if auto-generated incorrectly)

# 2. Test it
make test-integration
# (validates migration + model are in sync)

# 3. Commit both files
git add dashboard/models.py migrations/versions/<rev>.py
git commit -m "Add column to users table"
```

### New test

```bash
# Add test to tests/test_something.py or create new file
# Ensure fixtures from conftest.py are imported

# Run it
python -m pytest tests/test_something.py -v

# Run full suite to ensure no regressions
make check
```

### New tool (Composio integration)

When adding a Composio tool:

1. Add to `bot/tools.py` with full schema.
2. Add corresponding test in `tests/test_tools.py` (calls the live Composio API if `COMPOSIO_API_KEY` is set).
3. Add evaluation case in `scripts/eval_cases.py` to test the agent's decision to call it.
4. Run `make test`, `make test-composio` (if you have the key), and `make eval`.

## Debugging

### Failed test

```bash
# Run with verbose output and stop after first failure
python -m pytest tests/test_agent.py::test_name -vv -s -x

# Print statements show if you use -s flag
print("debugging value:", var)  # will display
```

### Failed eval

```bash
# Read the latest report
cat evals/latest.md

# Extract full transcript for one case
grep "case_name" evals/eval-<timestamp>.jsonl | jq .

# Rerun just that case with verbose logging
python -m scripts.eval --only case_name -v
```

### Docker-specific issue

```bash
# Run the test suite inside Docker to reproduce
make test-docker

# Or manually build and enter the image
docker compose -f compose.test.yaml build tests
docker compose -f compose.test.yaml run --rm tests sh
# Inside the container:
python -m pytest tests/test_something.py -v
```

### Database

```bash
# Connect to the test database (if it's still running)
psql postgresql://dobby:dobpy@127.0.0.1:5433/dobby_test

# Query conversation history
SELECT id, model, created_at FROM conversations ORDER BY created_at DESC LIMIT 5;

# Clean up
docker compose -f compose.test.yaml rm -sf postgres-test
```

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for live deployment (pushing to main, updating Pi/Windows instances).

## Key files for common tasks

| Task | File(s) |
| --- | --- |
| Add Discord command | `bot/main.py` (slash commands) |
| Add Composio tool | `bot/tools.py`, `tests/test_tools.py`, `scripts/eval_cases.py` |
| Change authorization | `bot/main.py` (user/role/channel checks), `tests/test_bot.py` (tests) |
| Change database schema | `dashboard/models.py`, create migration with `alembic` |
| Change voice/responses | `bot/voice.py`, `bot/responses/` (add to `.txt` files) |
| Change contact storage | `bot/contacts.py` |
| Add evaluation case | `scripts/eval_cases.py` |

## Testing checklist before opening a PR

- [ ] `make check` passes (lint + unit + integration + live API if key is present).
- [ ] New code has tests (or reason documented in commit).
- [ ] No credentials in logs or error messages.
- [ ] No breaking schema migrations (or clearly documented in PR).
- [ ] Commit message describes the **why**, not just the what.

## Common issues

| Issue | Solution |
| --- | --- |
| `ModuleNotFoundError: No module named 'bot'` | Run from repo root; ensure `.` is in `PYTHONPATH` (Makefile does this). |
| Postgres won't start | Ensure port 5433 is free; check `docker compose -f compose.test.yaml ps`. |
| `.env` or secrets missing | Run `cp .env.example .env` and `mkdir -p secrets data` first. |
| Test import errors | Run `make venv` again; dependencies may have changed in `requirements-dev.txt`. |
| Flaky async tests | Use `asyncio.timeout()` instead of sleeps; check test fixtures in `conftest.py`. |

## Staying in sync with main

```bash
# Pull latest changes
git fetch origin
git rebase origin/main

# Update dependencies if requirements*.txt changed
make venv

# Verify still works
make check
```

