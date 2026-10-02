# Dobby test environment. `make help` lists targets.

VENV := .venv
PY := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff
TEST_DB_URL := postgresql+asyncpg://dobby:dobby@127.0.0.1:5433/dobby_test

.PHONY: help venv test test-bot test-dashboard test-integration test-composio test-docker lint fmt check chat chat-fake chat-db eval

help:
	@echo "venv              create .venv and install all dev dependencies"
	@echo "test              bot unit suite + dashboard suite (offline, mocked)"
	@echo "test-bot          bot unit suite only"
	@echo "test-dashboard    dashboard API suite only"
	@echo "test-integration  postgres-backed suite (throwaway docker postgres)"
	@echo "test-composio     real Composio API sanity check (needs COMPOSIO_API_KEY in .env)"
	@echo "test-docker       sandboxed suite + lint inside the shipped image"
	@echo "chat              REPL against the real agent (needs MODAL/COMPOSIO keys in .env)"
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

# Real Composio API call (no postgres, no docker) — asserts the live tool
# schemas match bot.tools.TOOLS and carry no null `strict` field. Skips
# itself when COMPOSIO_API_KEY isn't set; `dotenv run` loads .env for just
# this invocation so a bare `pytest -q` never picks up a live credential.
test-composio:
	$(VENV)/bin/dotenv run -- $(PY) -m pytest tests/live -q

test-docker:
	docker compose -f compose.test.yaml run --rm tests
	docker compose -f compose.test.yaml run --rm lint

# Interactive chat with the agent pipeline (no Discord). Leaves the chat
# postgres running so history survives between sessions; stop it with:
#   docker compose -f compose.test.yaml rm -sf postgres-test
chat-db:
	docker compose -f compose.test.yaml up -d --wait postgres-test

chat: chat-db
	$(PY) -m scripts.chat

chat-fake: chat-db
	$(PY) -m scripts.chat --fake

# Behavioral evals: live model decisions, stubbed tool execution (nothing
# real happens). Needs MODAL_* keys in .env. Report lands in evals/.
eval: chat-db
	$(PY) -m scripts.eval

lint:
	$(RUFF) check bot scripts tests dashboard/tests
	$(RUFF) format --check bot scripts tests dashboard/tests

fmt:
	$(RUFF) format bot scripts tests dashboard/tests
	$(RUFF) check --fix bot scripts tests dashboard/tests

check: lint test test-integration test-composio
