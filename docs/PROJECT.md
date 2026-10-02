# Project Overview

Dobby is a Discord bot that works with Google Calendar, GitHub and Notion through Composio, driven by a Modal-hosted model behind an OpenAI-compatible endpoint. This document covers project structure, key decisions, and status.

## Project goals

1. **Usable by non-technical users:** Discord mentions and slash commands, no API keys required by users.
2. **Secure:** No secrets in images, read-only mounts, sandboxed execution, no credential leaks.
3. **Privacy-respecting:** Context only from the requesting channel/thread, no DM surveillance, rate-limited.
4. **Self-hosted:** Runs on personal Pi or Windows computer, no cloud hosting required.
5. **Single-instance:** One Discord token, one Google calendar, one running bot per host.

## Key decisions

| Decision | Rationale | Status |
| --- | --- | --- |
| Modal-hosted model (not local LLM) | Accuracy/reliability for structured output; Pi can't run 7B+ models. | ✓ Live |
| Composio for calendar/contact tools | Standardized schema, multi-tool support, reduces custom integration code. | ✓ Live (v3 migration done) |
| Postgres for agent history | Required by Composio SDK; enables conversation resumption, evals. | ✓ Live (local docker-compose only) |
| Discord mentions + slash commands | Hybrid approach: rich context from mentions, exact requests from commands. | ✓ Live |
| Preview + confirm pattern | Prevents accidental writes; users can review before calendar changes. | ✓ Live |
| One-hour default for new meetings | Empirically common; user specifies if different. | ✓ Live |
| Separate `requirements.txt` per component | Bot runtime stays lean; dashboard deps don't ship. | ✓ Live |
| Python 3.12 shipped, 3.14 host dev | Ship a stable version; dev/CI faster with latest. Ruff validates 3.12 compatibility. | ✓ Live |

## Architecture layers

| Layer | Purpose | Tech |
| --- | --- | --- |
| **Bot (Python)** | Discord lifecycle, request parsing, LLM tool-calling loop, calendar operations | discord.py, Modal API, Composio |
| **Agent** | Conversation state, tool dispatch, reasoning effort | Anthropic SDK, custom orchestration |
| **Dashboard (FastAPI)** | Optional web UI for settings, contact management | FastAPI, SQLAlchemy, Postgres |
| **Infrastructure** | Deployment, updates, secrets | Docker, Compose, GitHub Actions, systemd (Pi) |

See [ARCHITECTURE.md](../ARCHITECTURE.md) for detailed request flow.

## Current status

### Implemented ✓

- Create/update/delete calendar events via Discord mentions and slash commands.
- Fuzzy event matching for updates/deletes without IDs.
- Contact memory (add, list, remove by name).
- Authorization: server/role/user/channel allowlists.
- Conflict detection and ETag-based stale write prevention.
- Timezone handling and duration preservation.
- OpenAI-format tool calling against a scoped Composio tool set.
- Docker multi-architecture builds (amd64, arm64).
- Automatic Pi updates (optional systemd timer).
- Comprehensive test suite (unit, integration, Docker, evals).
- Dashboard API and UI (web-based settings, contacts).

### Known limitations ✗

- **Single instance per token:** Two instances with the same Discord token cause duplicate gateway events and confirmation collisions.
- **One calendar:** Always the primary/specified calendar. Multi-calendar support would require UI expansion.
- **No recurring events:** Edit only future timed events (< 24h). Recurring and all-day events are read-only.
- **Self-hosted model endpoint:** Latency and availability depend on the Modal deployment; reasoning models add latency to every reply.
- **No conferences:** Meeting links must be added manually or via Google Meet shortcuts.
- **No attendee availability check:** Conflict detection only checks the bot's calendar, not individual invitees.

### Missing / out of scope ✗

- **Cloud deployment:** Code supports it (stateless, env-configured). Workflow exists but disabled. Needs key management strategy.
- **Persistent sessions:** Confirmation buttons invalidate after restart (by design). Slack integration (would require threading from Discord messages).
- **Message drafts:** Dobby processes mentions immediately; no draft/approval workflow.
- **Custom AI models:** Modal endpoint is configurable but not exposed to users.
- **Multi-server:** One Discord token per bot; separate instances for separate servers.

## Files and responsibilities

See [TESTING.md](TESTING.md), [DEVELOPMENT.md](DEVELOPMENT.md), and [ARCHITECTURE.md](../ARCHITECTURE.md#source-structure) for detailed file listings.

### Bot core (`bot/`)

- `main.py` — Discord lifecycle, message handling, confirmation UI.
- `agent.py` — Agent initialization, tool dispatch, reasoning effort, cost tracking.
- `tools.py` — Composio tool definitions and argument validation.
- `config.py` — Environment loading, allowlist validation.
- `format.py` — Response formatting and error messages.
- `memory.py` — In-memory state (open questions, cooldowns).
- `contacts.py` — Contact memory (`data/contacts.json`), email prompts.

### Infrastructure

- `Dockerfile` — Multi-stage: base (deps), test (+ suite), runtime (shipped).
- `compose.yaml` — Dobby service + optional Postgres.
- `compose.auth.yaml` — One-shot OAuth helper for Google linking.
- `compose.test.yaml` — Test services + temporary Postgres.
- `.github/workflows/ci.yml` — CI validation (every push).
- `.github/workflows/publish.yml` — Multi-arch image build and publish (main only).
- `deploy/` — Optional Pi systemd timer for auto-updates.

### Tests and evals

- `tests/` — Unit suite (mocked external calls).
- `tests/integration/` — Postgres schema + ORM validation.
- `scripts/eval.py` — Evaluation runner (real model, stubbed tools).
- `scripts/eval_cases.py` — Test case definitions.
- `evals/` — Timestamped reports (JSON + Markdown).

### Dashboard (optional)

- `dashboard/main.py` — FastAPI app.
- `dashboard/routers/` — Settings, contact, auth endpoints.
- `dashboard/models.py` — SQLAlchemy schema.
- `migrations/` — Alembic database migrations.

## Development pace and review

- **Active development:** Bugs, safety improvements, new Composio tools.
- **Minimal breaking changes:** Schema migrations include reversals. Discord API upgrades are infrequent.
- **Review approach:** All PRs require passing CI; code review recommended for architecture changes or security-sensitive code.

## Support and troubleshooting

- **User docs:** [README.md](../README.md) (setup, usage, everyday Docker commands).
- **Technical docs:**
  - [ARCHITECTURE.md](../ARCHITECTURE.md) — Request flow, design decisions, limits.
  - [TESTING.md](TESTING.md) — Test layers, running locally.
  - [DEVELOPMENT.md](DEVELOPMENT.md) — Setup, making changes, debugging.
  - [DEPLOYMENT.md](DEPLOYMENT.md) — Production setup, auto-updates.
  - [DEPENDENCIES.md](DEPENDENCIES.md) — Dependency management, updates.
  - [CICD.md](CICD.md) — CI/CD pipeline, publishing, rollback.
  - [EVALS.md](EVALS.md) — Behavioral evaluation framework.
- **Troubleshooting:** See [README.md](../README.md#troubleshooting) for common issues.

## Future roadmap

Candidate improvements (not scheduled):

1. **Cloud deployment:** Re-enable Cloud Run workflow for users who prefer cloud hosting.
2. **Recurring events:** Support edit/creation of recurring meetings.
3. **Slack support:** Extend to Slack threads (same UI, different platform).
4. **Custom models:** Allow users to swap the Modal endpoint without code changes.
5. **Multi-calendar:** Support multiple calendars per bot instance.
6. **Meeting links:** Auto-generate Google Meet links on create.
7. **Integration testing with live APIs:** Currently evals stub tool results; live e2e tests would catch drift.

## Metrics and monitoring

### Local production (Pi/Windows)

- **Docker logs:** `docker compose logs --tail 50 -f dobby` shows bot events.
- **Errors:** Logged with action (e.g., `mention_access_denied`, `calendar_conflict_detected`).
- **Optionally:** Pipe logs to external service (Datadog, CloudWatch, etc.) via syslog.

### Evaluations

- `evals/latest.md` — Latest eval run summary (pass/fail counts, failure reasons).
- `evals/eval-<timestamp>.jsonl` — Full transcript (model inputs/outputs, tool calls).

### Missing

- **Uptime monitoring:** No alerting if bot goes down (local logs only).
- **Usage metrics:** No tracking of how many meetings are scheduled, who uses it, etc.
- **Cost tracking:** Model usage costs not exposed (part of the Modal bill).

## Dependencies and compatibility

- **Python:** 3.12 (shipped), 3.14+ (development).
- **Discord.py:** 2.3+.
- **Composio:** 3.x (migration from 2.x completed).
- **Anthropic SDK:** 0.46+.
- **Postgres:** 15+ (for local docker-compose; not needed for Discord-only bot).
- **Docker:** 20.10+ (multi-arch build support).

See [DEPENDENCIES.md](DEPENDENCIES.md) for full list and update procedures.

## Related projects

- **discord.py:** Python Discord API client.
- **Composio:** Tool framework (calendar, GitHub, Notion, etc.).
- **Modal:** LLM provider (OpenAI-compatible endpoint).
- **Anthropic SDK:** Agent and structured output support.

