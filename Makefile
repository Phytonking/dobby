# Dobby test environment. `make help` lists targets.

VENV := .venv
PY := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff
TEST_DB_URL := postgresql+asyncpg://dobby:dobby@127.0.0.1:5433/dobby_test

.PHONY: help venv test test-bot test-dashboard test-integration test-composio test-docker test-regression lint fmt check chat chat-fake chat-db eval

help:
	@echo "venv              create .venv and install all dev dependencies"
	@echo "test              bot unit suite + dashboard suite (offline, mocked)"
	@echo "test-bot          bot unit suite only"
	@echo "test-dashboard    dashboard API suite only"
	@echo "test-integration  postgres-backed suite (throwaway docker postgres)"
	@echo "test-composio     live: every curated Composio action still resolves (COMPOSIO_API_KEY)"
	@echo "test-docker       sandboxed suite + lint inside the shipped image"
	@echo "test-regression   system regression suite: built images, real postgres, browser"
	@echo "chat              REPL against the real agent (AI_* + COMPOSIO_API_KEY in .env)"
	@echo "chat-fake         REPL with scripted model/Composio — no keys, no network"
	@echo "eval              behavioral evals against the live model (stubbed tool execution)"
	@echo "lint / fmt        ruff check / ruff format"
	@echo "check             lint + all local suites (what CI runs)"

venv:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r requirements-dev.txt

test: test-bot test-dashboard

test-bot:
	$(PY) -m pytest -q

test-dashboard:
	$(PY) -m pytest dashboard/tests -q

# Throwaway postgres on 127.0.0.1:5433 (tmpfs — no state survives). The suite
# is skipped automatically when TEST_DATABASE_URL is unset, so this target is
# the only local entry point that actually runs it.
test-integration:
	docker compose -f compose.test.yaml up -d --wait postgres-test
	@status=0; \
	TEST_DATABASE_URL=$(TEST_DB_URL) $(PY) -m pytest tests/integration -q || status=$$?; \
	docker compose -f compose.test.yaml rm -sf postgres-test >/dev/null; \
	exit $$status

# Real Composio API call (no postgres, no docker): every curated action still resolves at the
# pinned toolkit versions. Skips itself without COMPOSIO_API_KEY; `dotenv run` loads .env for
# just this invocation so a bare `pytest -q` never picks up a live credential.
test-composio:
	$(VENV)/bin/dotenv run -- $(PY) -m pytest tests/live -q

# Upstream's end-to-end suite (test_regression/README.md): builds and runs the images.
test-regression:
	$(PY) test_regression/run.py

test-docker:
	docker compose -f compose.test.yaml run --rm tests
	docker compose -f compose.test.yaml run --rm lint

# Interactive chat with the agent pipeline (no Discord). Leaves the throwaway postgres
# running between sessions; stop it with:
#   docker compose -f compose.test.yaml rm -sf postgres-test
chat-db:
	docker compose -f compose.test.yaml up -d --wait postgres-test

chat: chat-db
	$(PY) -m scripts.chat

chat-fake: chat-db
	$(PY) -m scripts.chat --fake

# Behavioral evals: live model decisions, stubbed tool execution (nothing
# real happens). Needs AI_* settings and COMPOSIO_API_KEY (schemas only) in .env.
# Report lands in evals/.
eval: chat-db
	$(PY) -m scripts.eval

lint:
	$(RUFF) check bot scripts tests dashboard/tests
	$(RUFF) format --check bot scripts tests dashboard/tests

fmt:
	$(RUFF) format bot scripts tests dashboard/tests
	$(RUFF) check --fix bot scripts tests dashboard/tests

check: lint test test-integration test-composio
