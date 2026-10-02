# Dependency Management

How Dobby's dependencies are managed, updated, and validated.

## Dependency files

| File | Purpose | Who edits | How to update |
| --- | --- | --- | --- |
| `requirements.in` | Bot runtime deps (ranges allowed) | Dev | Edit and run `pip-compile` |
| `requirements.txt` | Locked bot runtime deps (exact versions) | Generated | `pip-compile requirements.in` |
| `requirements-dev.txt` | All dev + test + bot deps | Generated | `pip-compile requirements-dev.txt` (includes `requirements.in`) |
| `dashboard/requirements.txt` | Dashboard API deps (separated file) | Dev or CI | See below |

Python version: shipped image uses **Python 3.12**; host dev environment uses **3.14+**. Ruff's `target-version` in `pyproject.toml` is pinned to `py312` to catch syntax that won't work in the shipped image.

## How dependencies are locked

```bash
# Install pip-tools (once)
pip install pip-tools

# Generate/update requirements.txt from requirements.in
pip-compile requirements.in

# This produces exact versions and hashes for reproducibility
pip install -r requirements.txt
```

The locked file includes hash verification (`--require-hashes`) for supply-chain safety.

`requirements-dev.txt` is auto-generated from both `requirements.in` and the dev suite's direct imports. It's committed so `make venv` never has to recompile.

## Common updates

### Add a new bot runtime dependency

```bash
# Edit requirements.in, add the new package
nano requirements.in
# e.g., add: sqlalchemy>=2.0

# Lock it
pip-compile requirements.in

# Test
make venv
make check

# Commit both files
git add requirements.in requirements.txt requirements-dev.txt
git commit -m "Add sqlalchemy for query building"
```

### Update an existing dependency

```bash
# Pin the new version in requirements.in
nano requirements.in
# e.g., change: discord.py>=2.3.0 to discord.py>=2.4.0

# Lock and test
pip-compile requirements.in
make venv
make check

# Commit
git add requirements.in requirements.txt requirements-dev.txt
git commit -m "Update discord.py to 2.4.0"
```

### Update all dependencies to latest compatible versions

```bash
pip-compile --upgrade requirements.in
make venv
make check

# Review changes in requirements.txt; commit if tests pass
git add requirements.in requirements.txt requirements-dev.txt
git commit -m "Upgrade all dependencies to latest"
```

### Add a dashboard-only dependency

Dashboard dependencies are in a separate file to keep the bot image lean. Add to `dashboard/requirements.txt`:

```bash
# Edit dashboard/requirements.txt directly
nano dashboard/requirements.txt

# Test the dashboard
python -m pytest dashboard/tests -v

# Commit
git add dashboard/requirements.txt
git commit -m "Add dependency for dashboard feature"
```

## Security and supply chain

1. **Hashes:** Locked `requirements.txt` includes SHA256 hashes. `pip install --require-hashes` prevents tampering.
2. **Lockfile freshness:** Requirements are committed to Git so all environments use the same versions.
3. **Audit:** Run `pip-audit` to check for known vulnerabilities:
   ```bash
   pip install pip-audit
   pip-audit -r requirements.txt
   ```
4. **Python version:** Shipped image uses Python 3.12 exactly (specified in `Dockerfile`). Ruff validates code doesn't use 3.13+ features.

## CI validation

`.github/workflows/ci.yml`:

1. Builds Docker image with `pip install -r requirements.txt` (locked versions only).
2. Runs tests in both local Python 3.14 environment and Docker Python 3.12 image.
3. Publishes multi-architecture images if all checks pass.

If `requirements.txt` is updated but not committed, the build fails (locked file required).

## Dependency conflicts

If `pip-compile` reports conflicts:

```bash
# Show the conflict details
pip-compile --dry-run requirements.in
```

Options:
1. **Loosen constraints:** Edit `requirements.in` to allow a broader version range.
2. **Pin a specific version:** Add a constraint that both packages accept.
3. **Replace the package:** Switch to an alternative with fewer conflicts.

Example:
```
# Before (conflict)
discord.py>=2.3.0
some-package-that-needs-discord-py-2.2

# After (resolved)
discord.py>=2.3.0,<3.0
some-package-that-needs-discord-py-2.2
```

## Dashboard dependencies

Dashboard dependencies live separately in `dashboard/requirements.txt` so they don't bloat the bot runtime image. The bot image never installs them.

When a dashboard dependency changes:
1. Update `dashboard/requirements.txt` (manual, no pip-tools).
2. Test locally: `pip install -r dashboard/requirements.txt && pytest dashboard/tests`.
3. Commit `dashboard/requirements.txt`.

CI automatically installs dashboard deps when running `dashboard/tests`.

## Python version strategy

- **Shipped:** Python 3.12.x (exact; specified in `Dockerfile`)
- **Development:** Python 3.14+ (for faster local testing)
- **Ruff validation:** Target 3.12 (`pyproject.toml:target-version`) so no 3.13-only syntax reaches the image

If a dependency requires Python 3.13+, add a comment in `requirements.in` and bump the shipped image version (also updates `Dockerfile`).

## Troubleshooting

| Issue | Solution |
| --- | --- |
| `pip-compile` not found | Install: `pip install pip-tools` |
| Hash mismatch in `requirements.txt` | Rerun `pip-compile requirements.in`; manually editing hashes is not supported |
| Dependency not in locked file | Check it's listed in `requirements.in`; rerun `pip-compile` |
| Tests fail after `pip install -r requirements.txt` | A test may import a dev-only package; ensure it's in `requirements-dev.txt` or skip the test unless dev deps are installed |
| Circular dependency in `requirements.in` | Edit `requirements.in` to break the cycle; `pip-compile --dry-run` shows which packages conflict |
| Dashboard tests fail | Ensure `dashboard/requirements.txt` is up-to-date; run `pip install -r dashboard/requirements.txt` |

## Related docs

- [TESTING.md](TESTING.md): How tests run and which dependencies they need.
- [DEVELOPMENT.md](DEVELOPMENT.md): Setting up `make venv` and local development.
- [ARCHITECTURE.md](../ARCHITECTURE.md): Runtime and image structure.

