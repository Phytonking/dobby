# Documentation Index

Complete guide to Dobby's documentation.

## For users

**Start here** if you're setting up or using Dobby.

- [**README.md**](../README.md) — Setup guide (6 steps), usage examples, everyday Docker commands, troubleshooting.
- [**ARCHITECTURE.md**](../ARCHITECTURE.md) — How it works: request flow, design decisions, limits, security model.

## For developers

**Start here** if you're contributing code or fixing bugs.

### Getting started

- [**DEVELOPMENT.md**](DEVELOPMENT.md) — Setup, testing workflow, debugging, common issues.
- [**TESTING.md**](TESTING.md) — Test strategy (unit, integration, Docker, evals), running tests, adding tests.
- [**DEPENDENCIES.md**](DEPENDENCIES.md) — Dependency management, updating, security.

### Working on specific tasks

- [**Bot/Discord changes**](DEVELOPMENT.md#key-files-for-common-tasks) — Add commands, change authorization.
- [**Database/schema changes**](DEVELOPMENT.md#schema-changes-database-migrations) — Migrations, testing.
- [**Composio/tools**](DEVELOPMENT.md#new-tool-composio-integration) — Add integrations, test them.
- [**Evaluations**](EVALS.md) — Run behavioral evals, add test cases, interpret results.

## For maintainers

**Start here** if you're releasing, deploying, or managing the project.

- [**CICD.md**](CICD.md) — CI/CD pipeline, publishing images, rollback, troubleshooting.
- [**DEPLOYMENT.md**](DEPLOYMENT.md) — Production setup (Pi/Windows), automatic updates, monitoring.
- [**PROJECT.md**](PROJECT.md) — Project status, decisions, roadmap, limitations.

## Document map

| Document | Audience | Topics |
| --- | --- | --- |
| [README.md](../README.md) | Users, new devs | Setup, usage, Docker commands, troubleshooting |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Devs, reviewers | Design, request flow, security, limits |
| **DEVELOPMENT.md** | Devs | Local setup, testing, making changes, debugging |
| **TESTING.md** | Devs, reviewers | Test strategy, running tests, adding tests |
| **EVALS.md** | Devs, reviewers | Behavioral evaluation, test cases, interpreting results |
| **DEPENDENCIES.md** | Devs, maintainers | Dependency management, updates, security |
| **CICD.md** | Maintainers, DevOps | CI/CD pipeline, publishing, rollback |
| **DEPLOYMENT.md** | Maintainers, ops | Production setup, auto-updates, monitoring |
| **PROJECT.md** | Maintainers, architects | Project status, decisions, roadmap |
| **INDEX.md** | Everyone | Navigation (this file) |

## Quick reference: Make targets

All development tasks use `make`:

```bash
make help                # Show all targets
make venv                # Install dev environment
make test                # Unit + dashboard tests
make test-integration    # Postgres-backed integration tests
make test-docker         # Sandboxed suite inside shipped image
make test-composio       # Real Composio API sanity check
make chat                # Interactive REPL with real model
make chat-fake           # Interactive REPL with scripted responses
make eval                # Behavioral evaluations
make lint                # Ruff check
make fmt                 # Ruff format + auto-fix
make check               # lint + test + test-integration + test-composio
```

## Common workflows

### I want to...

**Fix a bug**
1. Reproduce it with a unit test in `tests/`.
2. Fix the code in `bot/` or `dashboard/`.
3. Run `make check` to validate.
4. Commit with a clear message and push.

**Add a new feature**
1. Sketch the change and test it: `make test`, `make eval` (optional).
2. Write tests first (TDD), then code.
3. Update ARCHITECTURE.md or docs if the design changes.
4. Run `make check` before pushing.

**Set up for development**
1. `make venv` — install dependencies.
2. `cp .env.example .env` — create config.
3. `docker compose -f compose.test.yaml up -d --wait postgres-test` — start Postgres for chat/evals.
4. `make test` to verify setup.

**Add a Composio tool**
1. Define it in `bot/tools.py`.
2. Add test case in `tests/test_tools.py`.
3. Add evaluation case in `scripts/eval_cases.py`.
4. Run `make test`, `make test-composio`, `make eval`.

**Deploy a change**
1. Push to a branch; CI validates.
2. Open PR, get review.
3. Merge to main; `publish.yml` builds and publishes images.
4. Pi with auto-updates pulls within ~5 minutes (optional).
5. Or manually pull on Windows/Pi with `docker compose up -d --no-build`.

**Rollback a bad change**
1. `git revert <bad-commit>` on main, or
2. Manually pull a previous image: `DOBBY_IMAGE=ghcr.io/phytonking/dobby:sha-<old-sha> docker compose up -d`.

## Where to find things

| Question | Answer |
| --- | --- |
| How do I set up locally? | [DEVELOPMENT.md](DEVELOPMENT.md) |
| How do I run tests? | [TESTING.md](TESTING.md) |
| How does the bot work? | [ARCHITECTURE.md](../ARCHITECTURE.md) |
| What are the limitations? | [PROJECT.md](PROJECT.md#known-limitations) or [ARCHITECTURE.md](../ARCHITECTURE.md#limitations) |
| How do I add a feature? | [DEVELOPMENT.md](DEVELOPMENT.md#making-a-change) |
| How do I evaluate changes? | [EVALS.md](EVALS.md) |
| How do I deploy? | [DEPLOYMENT.md](DEPLOYMENT.md) |
| What's the CI/CD pipeline? | [CICD.md](CICD.md) |
| How do I manage dependencies? | [DEPENDENCIES.md](DEPENDENCIES.md) |
| Where is the bot image published? | [CICD.md](CICD.md#publishing-publishyml) |
| How do I troubleshoot? | [README.md](../README.md#troubleshooting) |

## Navigation by role

### I'm a user setting up Dobby

Read in order:
1. [README.md](../README.md#set-up-dobby)
2. [README.md](../README.md#how-to-use-dobby)
3. [README.md](../README.md#troubleshooting) (if something goes wrong)
4. [DEPLOYMENT.md](DEPLOYMENT.md) (for Pi automatic updates)

### I'm a developer joining the team

Read in order:
1. [README.md](../README.md) (overview)
2. [DEVELOPMENT.md](DEVELOPMENT.md) (local setup)
3. [TESTING.md](TESTING.md) (how tests work)
4. [ARCHITECTURE.md](../ARCHITECTURE.md) (how the bot works)
5. Make a small fix (see "Fix a bug" workflow above)

### I'm a code reviewer

Consult:
- [ARCHITECTURE.md](../ARCHITECTURE.md) (design questions)
- [TESTING.md](TESTING.md) (test coverage questions)
- [DEPENDENCIES.md](DEPENDENCIES.md) (for dependency changes)
- [EVALS.md](EVALS.md) (for behavior changes)

### I'm a maintainer releasing a version

Consult:
- [CICD.md](CICD.md) (publishing process)
- [PROJECT.md](PROJECT.md) (roadmap, status)
- [DEPENDENCIES.md](DEPENDENCIES.md) (managing security updates)
- [DEPLOYMENT.md](DEPLOYMENT.md#deployment-guide) (production checklist)

## Documentation standards

Dobby docs follow these conventions:

- **Clear headings:** Sections organize by purpose (setup, troubleshooting, etc).
- **Quick start:** Each doc leads with the essential command(s).
- **Tables:** Reference info (commands, files, decisions) lives in tables.
- **Examples:** Code and shell examples show real usage.
- **Links:** Cross-references use relative paths for GitHub/local rendering.
- **No duplication:** Each fact lives in one place; others link to it.

## Reporting issues and suggesting improvements

Found an error in the docs? Have a better way to explain something?

1. Check if it's already documented elsewhere (search the `.md` files).
2. Open an issue describing what's unclear or missing.
3. Propose a fix with a PR if you have one.

Documentation pull requests are always welcome.

