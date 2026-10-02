# CI/CD and Release Process

How changes move from a local branch to production (GitHub Container Registry, Pi, Windows).

## Release flow

```
Developer push → GitHub Actions CI → Test + build → Registry publish → 
Optional auto-pull (Pi) → Running instance updates
```

## CI: `.github/workflows/ci.yml`

Runs on every push to any branch and on PRs.

### What it does

1. **Compose validation:** Syntax check all `.yaml` files.
2. **Docker build:** Build both `linux/amd64` and `linux/arm64` images.
3. **Unit tests:** Run `compose.test.yaml` suite (no network, no credentials).
4. **Linting:** Ruff check/format over bot, scripts, tests, dashboard.

### What it does NOT do

- Does not publish to the registry (main branch only).
- Does not touch Discord, Google, or Modal (sandboxed suite).
- Does not run integration tests in CI (integration suite runs locally before push).

### Success/failure

- **Pass:** All checks pass → green checkmark on PR.
- **Fail:** Any step fails → red X, blocks merge.

If CI fails, fix the issue locally (`make check`) and push again.

## Publishing: `.github/workflows/publish.yml`

Runs **only on pushes to main** after all CI checks pass.

### What it does

1. Runs CI checks again (faster cached build).
2. Builds multi-architecture images (`linux/amd64`, `linux/arm64`).
3. Tags with:
   - `latest` (always).
   - `sha-<commit-sha>` (for rollback identification).
4. Publishes to GitHub Container Registry (`ghcr.io/phytonking/dobby`).

GitHub uses its built-in `GITHUB_TOKEN` for authentication; no separate registry credentials needed.

### Image visibility

Make the package **public** in GitHub:

1. Go to the package settings (the package page on GitHub).
2. Set visibility to **Public**.
3. Unauthenticated Pi/Windows pulls now work.

### Deployment

After publish completes:

- **Pi with auto-updates:** Optional systemd timer checks every 5 minutes, auto-pulls when a new image is available (see [DEPLOYMENT.md](DEPLOYMENT.md#automatic-updates-on-the-pi)).
- **Pi manual pull:** `docker compose -f compose.yaml -f compose.registry.yaml pull dobby && docker compose up -d`.
- **Windows:** Manual `docker pull` and `docker compose up -d`.

## Making a release

### 1. Prepare locally

```bash
# Create a branch for your changes
git checkout -b feature/something

# Make changes and test
make check    # includes all local tests

# Commit with a clear message
git commit -m "Add feature: something"
```

### 2. Push and verify CI

```bash
# Push the branch
git push origin feature/something

# GitHub Actions CI runs automatically
# Watch at: https://github.com/phytonking/dobby/actions
```

When CI passes, open a PR to review before merging main.

### 3. Merge to main

```bash
# After PR approval, merge to main (via GitHub web UI or CLI)
gh pr merge <pr-number> --squash  # or --merge, depending on project preference

# Or merge locally
git checkout main
git pull origin main
git merge feature/something
git push origin main
```

GitHub Actions automatically:
1. Runs publish.yml.
2. Builds and publishes images.
3. Tags with commit SHA.

### 4. Deploy

If your instance has the optional auto-update timer enabled, it pulls within 5 minutes. Otherwise, manually pull:

```bash
# Pi or Windows
docker compose -f compose.yaml -f compose.registry.yaml pull dobby
docker compose up -d --no-build
```

Monitor logs:
```bash
docker compose logs --tail 50 -f dobby
```

## Rollback

If a bad image is published:

### Option 1: Revert and re-push (recommended)

```bash
# On main
git revert <bad-commit-sha>
git push origin main
# CI builds and publishes a new image
```

### Option 2: Manual rollback (immediate)

```bash
# Pull a previous image by tag
docker compose -f compose.yaml -f compose.registry.yaml down
DOBBY_IMAGE=ghcr.io/phytonking/dobby:sha-<good-commit-sha> docker compose up -d

# Update docker-compose.yaml if you want to persist the change
```

### Option 3: Disable auto-updates while investigating

Pi users with auto-updates enabled:
```bash
sudo systemctl stop dobby-update.timer
sudo systemctl disable dobby-update.timer
# When ready, re-enable with --enable instead of --disable
```

## Secrets and access control

- **No credentials in CI:** Discord token, Google API credentials, and Modal keys are never in `.github/workflows/` files.
- **No secrets leaked:** CI suite mocks all external calls, so no secrets are needed to run tests.
- **Registry auth:** GitHub's built-in `GITHUB_TOKEN` handles authentication; no manual registry credentials.
- **Branch protection:** Set main branch to require status checks passing before merge.

## Debugging CI failures

### Compose validation fails

Check syntax:
```bash
docker compose -f compose.yaml config  # syntax check
docker compose -f compose.test.yaml config
```

### Build fails

1. Check the image Dockerfile locally:
   ```bash
   docker build . --target runtime
   ```
2. If it works locally but not in CI, the issue may be architecture-specific. Check both:
   ```bash
   docker build . --platform linux/amd64 --target runtime
   docker build . --platform linux/arm64 --target runtime  # requires buildx
   ```

### Tests fail in CI but pass locally

1. CI runs in Python 3.14 (for speed) and also validates Python 3.12 (shipped image).
2. Check if the code uses Python 3.13+ features:
   ```bash
   python -m pytest tests -v  # local
   ```
   vs.
   ```bash
   make test-docker  # inside Python 3.12 image
   ```
3. If only Docker fails, the code likely uses 3.13+ syntax. Ruff checks with `target-version=py312`.

### Linting fails

Ruff finds formatting or lint issues:
```bash
make fmt   # auto-fix
make lint  # check only
```

## Scheduled tasks (future)

Currently, CI/publish workflows are event-driven (push to main). Future options:

1. **Scheduled lint/test:** Run nightly to catch dependency issues.
2. **Scheduled security audit:** `pip-audit` on locked requirements.
3. **Dependency updates:** Automated PR for security patches.

## Related docs

- [DEPLOYMENT.md](DEPLOYMENT.md): Production deployment, auto-update setup.
- [TESTING.md](TESTING.md): Test suites that CI runs.
- [DEPENDENCIES.md](DEPENDENCIES.md): Locked dependency format and updates.

