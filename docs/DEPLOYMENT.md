# Docker deployment: Raspberry Pi and Windows

Dobby runs on Windows Docker Desktop (`linux/amd64`) and 64-bit Raspberry Pi OS (`linux/arm64`). Use [README.md](../README.md#set-up-dobby) for initial Discord/Gemini/Google setup. This deployment needs no paid cloud server or Secret Manager.

## Prepare the Pi

Install 64-bit Raspberry Pi OS and Docker Engine with Compose using [Docker's Debian instructions](https://docs.docker.com/engine/install/debian/). Pi 4/5 with at least 2 GB RAM is recommended. Confirm:

```bash
uname -m
docker compose version
sudo systemctl enable --now docker
```

Architecture should be `aarch64`. Docker commands assume your trusted user can access Docker, or you run them through `sudo`. Docker group membership is effectively root access.

For the optional timer, keep the checkout at `/opt/dobby`:

```bash
sudo mkdir -p /opt/dobby
sudo chown "$(id -u):$(id -g)" /opt/dobby
git clone https://github.com/YOUR_OWNER/YOUR_REPO.git /opt/dobby
cd /opt/dobby
cp .env.example .env
mkdir -p secrets data
chmod 700 secrets data
chmod 600 .env
id -u
id -g
```

Set `DOBBY_UID`/`DOBBY_GID` in `.env` to those IDs, along with the API settings from the README. Compose file secrets preserve host permissions; do not make credentials world-readable to work around a UID mismatch.

## Move from Windows to the Pi

Complete Google authorization on Windows with the Docker helper. Transfer configuration and token over SSH/SCP, not Git. In PowerShell, replacing the destination:

```powershell
scp .env piuser@raspberrypi.local:/opt/dobby/.env
scp secrets/google-token.json piuser@raspberrypi.local:/opt/dobby/secrets/google-token.json
```

Create the destination directories first. Edit the transferred Pi `.env` to match the Pi UID/GID instead of Windows defaults. On Pi:

```bash
cd /opt/dobby
chmod 600 .env secrets/google-token.json
docker compose up -d --build
docker compose logs --tail 50 -f dobby
```

**Stop Windows Dobby first** with `docker compose down`. The runtime does not need `google-client.json`; retain it privately on the computer used for relinking. Both runtime files should be owned by the configured UID/GID.

A Pi with a browser can run the authorization helper itself. On a headless Pi, the Windows-and-transfer method is simpler. The helper callback only accepts host-local connections; do not open its port to the network.

## Test locally in Docker

Before deploying to either machine, confirm the image is sound. These run with networking disabled and need no credentials:

```text
docker compose -f compose.test.yaml run --rm tests
docker compose -f compose.test.yaml run --rm lint
```

`compose.test.yaml` builds the Dockerfile's `test` stage, which adds the suite on top of the same base layers the runtime uses. The deployed stage is last in the Dockerfile, so a plain `docker build .` and `docker compose up` still select the runtime image, which contains no tests.

Once `.env` and `secrets/google-token.json` exist, validate them without contacting Discord:

```text
docker compose run --rm --no-deps dobby python -m bot.main --check
```

Exit `0` prints `config_ok`; exit `2` names the setting to fix. Do this before the first `up` on a new machine, because `restart: unless-stopped` otherwise retries a misconfigured container indefinitely.

Create `.env` and the `secrets` directory **before** the first `up`. Docker creates a directory when a Compose secret's source file is missing, and that directory then mounts over the runtime credential path. The preflight names this case directly. `python scripts/bootstrap.py` creates both correctly and prints the UID/GID to set on Pi.

## Run on either machine

```text
docker compose up -d --build
docker compose ps
docker compose logs --tail 50 -f dobby
```

The runtime exposes no ports, runs non-root, drops capabilities, has a read-only filesystem and credentials, rotates logs, and caps memory at 512 MB. It restarts unless explicitly stopped. Windows Docker Desktop must remain running; on Pi Docker starts at boot.

`docker compose down` removes the container, not credentials. After changing credentials, recreate with `docker compose up -d --force-recreate --no-build` so replaced files are remounted. A restart alone may retain an old bind-mounted file.

## Publish images from GitHub

`.github/workflows/publish.yml` runs on main pushes. It tests the source, validates Compose, then builds/publishes AMD64 and ARM64 images to:

- `ghcr.io/owner/repository:main`
- `ghcr.io/owner/repository:sha-COMMIT_SHA`

The workflow uses GitHub's built-in `GITHUB_TOKEN`; no cloud key or application secret is required. PR checks have no package-write permission. Protect main and workflow changes: a trusted image can read runtime credentials.

After the first publish, open the package's GitHub settings and change its visibility to **Public**. A public repository does not necessarily make a new package public. Public pulls require no stored GitHub credential on the Pi. [Container Registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## Pull a published image on Windows or Pi

Set this in `.env`, using lowercase owner/repository names:

```dotenv
DOBBY_IMAGE=ghcr.io/your_owner/your_repo:main
```

Then run:

```text
docker compose -f compose.yaml -f compose.registry.yaml pull dobby
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build --pull never dobby
```

This reuses the same local secrets and service. `docker compose logs` and `docker compose down` still work because the project name is fixed. Use both files for starts/recreates in registry mode so you do not switch back to local builds. Docker selects the architecture automatically.

## Automatic updates on the Pi

First verify the published-image commands work. Keep the checkout at `/opt/dobby` with `DOBBY_IMAGE` set, then install the timer:

```bash
cd /opt/dobby
sudo install -m 644 deploy/dobby-update.service /etc/systemd/system/dobby-update.service
sudo install -m 644 deploy/dobby-update.timer /etc/systemd/system/dobby-update.timer
sudo systemctl daemon-reload
sudo systemctl enable --now dobby-update.timer
sudo systemctl start dobby-update.service
systemctl list-timers dobby-update.timer
sudo journalctl -u dobby-update.service -n 30 --no-pager
```

Every five minutes plus a small randomized delay, the timer runs the trusted local script as root to access Docker. It pulls first: if that fails, the current container stays running. Compose recreates the service when the image/config changed. `flock` prevents concurrent updates. No automatic Git pull occurs; Compose and updater files change only through a deliberate checkout update.

No router ports, webhook endpoint, self-hosted GitHub runner, or container-mounted Docker socket are needed. Workflow completion plus the next timer check determines update time. Updates briefly reconnect Discord and invalidate pending previews.

Only trusted administrators should be able to modify `/opt/dobby`, since the root timer reads its Compose files. Protect the GitHub main branch similarly. Docker restarts a crashed new image but does not automatically roll back to an old one.

Old images are not pruned automatically. Periodically inspect `docker system df` and remove specific unused Dobby images if space is tight.

To stop updates:

```bash
sudo systemctl disable --now dobby-update.timer
sudo systemctl stop dobby-update.service
```

Disable the timer **before** stopping Dobby for maintenance; otherwise the next check starts it again.

## Rollback and configuration updates

Disable the timer, set `DOBBY_IMAGE` to a known-good `:sha-COMMIT_SHA` tag, then run the published-image pull/up commands. This rolls back code, not Calendar operations. A fixed SHA tag prevents newer main pushes from changing the selected version.

Review and run `git pull --ff-only` manually for Compose/updater changes. Reinstall changed systemd units and run `sudo systemctl daemon-reload`. Ignored `.env` and secrets are preserved; do not copy the example over your existing `.env` during updates.

## Live smoke test

### Verify a local-image update

The September 10 diagnostic showed `dobby:local` still sending the legacy
`response_schema`, despite the source fix. Restarting a container does not rebuild
the Python files copied into its image. From the Pi checkout, after the fixes have
been committed and pushed:

```bash
git pull --ff-only
docker compose up -d --build --force-recreate dobby
docker compose logs --tail 50 dobby
docker compose exec dobby python -c "import inspect; from bot.planner import Planner; print(inspect.getsource(Planner))"
```

Confirm the startup log contains `structured_output=response_json_schema
planner_attempts=3` and the running planner uses
`response_json_schema=Plan.model_json_schema()`. For registry deployments, use the
published-image pull/up procedure above instead of building `dobby:local`.

The old diagnostic's explicit `response_schema=Plan` negative test will still fail
with HTTP 400; it is not the production path. A 503 from the JSON Schema path is
Gemini overload, not schema rejection. Planning now makes up to three attempts
with exponential backoff and jitter for transient failures, then returns an
actionable error in the requesting channel. Persistent overload can still prevent scheduling.
Calendar writes are not retried by this policy. Discord voice-library warnings
are unrelated to this text-only bot.

### Exercise Discord and Calendar

1. Without an allowed role/user grant, try `/events` and a mention; no calendar data or writes should result.
2. As an allowed user, preview a future meeting, check the one-hour default, cancel, and verify no event exists.
3. Create and confirm; check `/events` and Google Calendar for one event.
4. Test a 30-minute override and recent-message context.
5. Rename/reschedule by ID. Prepare an edit, change the event directly in Google Calendar, then confirm the old preview; it should fail as stale.
6. Remove the scheduler role before confirming a channel preview; access should be denied.
7. Restart Docker Dobby; verify `bot_ready` and that old previews cannot write.
8. If updates are enabled, push a harmless documentation change and verify publication plus the Pi update.

## Migrating away from Google Cloud

The Cloud Run workflow is removed, so new pushes no longer deploy there. This does not remove old workers, images, secrets, or IAM grants. If you created any, stop/remove the cloud worker before starting the Pi with the same token and clean up unused resources. Existing cloud resources may still accrue charges until stopped/deleted.
