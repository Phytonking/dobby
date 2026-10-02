# Docker deployment: Raspberry Pi and Windows

Dobby runs on Windows Docker Desktop (`linux/amd64`) and 64-bit Raspberry Pi OS (`linux/arm64`). Use [README.md](../README.md#set-up-dobby) for the Discord, AI model, Composio and dashboard-login setup; this page covers the machine itself. No paid cloud server is needed.

## Prepare the Pi

Install 64-bit Raspberry Pi OS and Docker Engine with Compose using [Docker's Debian instructions](https://docs.docker.com/engine/install/debian/). A Pi 4/5 with 4 GB RAM is recommended: Postgres, the API and the Next.js frontend run beside the bot. Confirm:

```bash
uname -m                      # aarch64
docker compose version
sudo systemctl enable --now docker
```

Docker group membership is effectively root access; give it only to a trusted user or run through `sudo`.

For the optional update timer, keep the checkout at `/opt/dobby`:

```bash
sudo mkdir -p /opt/dobby
sudo chown "$(id -u):$(id -g)" /opt/dobby
git clone https://github.com/YOUR_OWNER/YOUR_REPO.git /opt/dobby
cd /opt/dobby
cp .env.example .env
chmod 600 .env
id -u; id -g
```

Set `DOBBY_UID`/`DOBBY_GID` in `.env` to those IDs, then fill in the rest per the README.

## Move from Windows to the Pi

Transfer `.env` for configuration. Service connections live in Composio, but users, local password hashes and sessions live in Postgres; preserve them with a database backup/restore, or create new local accounts on the Pi. Transfer configuration over SSH/SCP, never Git:

```powershell
scp .env piuser@raspberrypi.local:/opt/dobby/.env
```

On the Pi, edit the transferred `.env`: set the Pi UID/GID, and change `DASHBOARD_URL` / `API_URL` to how browsers will reach the Pi (for example `http://raspberrypi.local:3000` and `http://raspberrypi.local:8000`). If `AUTH_MODE=oauth` or `both`, add the API host to the OAuth redirect URIs in Google Cloud and the Discord application. Local-only login does not need those OAuth applications. Then:

```bash
cd /opt/dobby
docker compose up -d --build
docker compose logs --tail 50 -f bot dashboard
```

**Stop Windows Dobby first** (`docker compose down`): one bot instance per Discord token. The Postgres data does not move automatically; either recreate the local admin with the CLI and re-add users, or restore a `pg_dump` from Windows (`docker compose exec -T postgres psql -U dobby dobby < backup.sql` after the first start).

## Dashboard authentication and LAN access

Choose `AUTH_MODE=oauth` (default), `local`, or `both` in `.env`. See the [README login walkthrough](../README.md#step-5-dashboard-login) for credentials and first-admin setup. Local mode needs no Google/Discord OAuth client credentials or bootstrap email; keep `SECRET_KEY` and the full stack's bot/database/integration settings configured.

For access from another computer, use the server's LAN address in both URL settings, for example:

```dotenv
AUTH_MODE=local
DASHBOARD_URL=http://192.168.1.50:3000
API_URL=http://192.168.1.50:8000
```

Use the same hostname/IP from both computers. `API_URL` must also be reachable from the frontend container for its server-side session check. Rebuild after changing it. On upgrade, build the migration image and apply migration 003 before starting the new dashboard (required in every auth mode):

```sh
docker compose build migrate dashboard frontend
docker compose run --rm migrate
docker compose up -d dashboard frontend
docker compose exec dashboard python -m dashboard.local_admin leona
```

The last command creates/promotes a local admin and prompts for a 12-256 character password twice. Repeat it to reset the password and revoke that user's existing sessions. Append `--email you@gmail.com` to attach credentials to an existing Google user instead of creating a separate account. Then visit `http://192.168.1.50:3000/login` from either machine.

Allow inbound TCP 3000/8000 only from your local subnet on the host's private network profile. Local authentication checks private/loopback source IPs, but Docker/proxies may mask clients, so this check does not replace the firewall. Plain HTTP does not encrypt credentials or cookies; use it only on a trusted LAN or configure HTTPS. `ENV=production` enables Secure cookies and requires HTTPS; the supplied Compose file does not pass `ENV`, so add it explicitly when configuring TLS.

Changing only `AUTH_MODE` needs `docker compose up -d --force-recreate dashboard`. `both` retains local login and adds OAuth; `oauth` disables local sessions; `local` disables OAuth sessions. OAuth login is separate from Composio's service authorization, which is still needed for integrations in any dashboard mode.

## Test locally in Docker

Before deploying, confirm the image is sound. These run with networking disabled and need no credentials:

```text
docker compose -f compose.test.yaml run --rm tests
docker compose -f compose.test.yaml run --rm lint
```

`compose.test.yaml` builds the Dockerfile's `test` stage on the same base layers the runtime uses. The deployed `runtime` stage is last in the Dockerfile, so a plain `docker build .` selects an image with no tests.

Once `.env` exists, validate the bot's settings without contacting Discord:

```text
docker compose run --rm --no-deps bot python -m bot.main --check
```

Exit `0` prints `config_ok`; exit `2` names the setting to fix. `docker compose config --quiet` additionally checks that the dashboard's required variables are set. Do both before the first `up` on a new machine, because `restart: unless-stopped` otherwise retries a misconfigured container indefinitely.

If `.env` is missing when you run `docker compose`, Docker may create a **directory** named `.env`; delete it and recreate the file (`python scripts/bootstrap.py` does this).

## Recover from the missing psycopg2 migration error

Older `migrations/env.py` removed `+asyncpg` from the database URL, causing SQLAlchemy to request the uninstalled `psycopg2` driver. The updated migration environment uses the installed `asyncpg` driver through Alembic's async-engine support. After updating the checkout, rebuild the migration image and retry:

```sh
docker compose build migrate
docker compose run --rm migrate
docker compose up -d dashboard frontend
```

This retry preserves the Postgres volume. A missing-driver error occurs before connecting to the database; there is no need to delete the volume or install packages interactively inside a container. The separate orphan-container warning does not cause this migration failure. Review the named old service before removing it, especially if it is an older running bot.

## Run on either machine

```text
docker compose up -d --build
docker compose ps
docker compose logs --tail 50 -f bot
```

`migrate` runs Alembic and exits; `bot`, `dashboard` and `frontend` then start. The bot exposes no ports, runs non-root with all capabilities dropped, a read-only filesystem, rotated logs and a 512 MB memory cap. The dashboard publishes 8000 and the frontend 3000 on the host; restrict them to your local subnet with the host firewall for local login. Do not expose local login through public port forwarding, tunnels or reverse proxies; they can hide the original client address. Windows Docker Desktop must remain running; on the Pi, Docker starts at boot.

`docker compose down` removes containers, not the `pgdata` volume or `.env`. After changing `.env`, recreate with `docker compose up -d --force-recreate --no-build`; if `API_URL` changed, also `docker compose build frontend`.

## Publish images from GitHub

`.github/workflows/ci.yml` runs on pull requests, main pushes, and manual dispatch.
Only a successful main run calls `.github/workflows/publish.yml`, after both the
unit/lint/Docker checks and the full system regression suite pass. It publishes
AMD64 and ARM64 **bot** images to:

- `ghcr.io/owner/repository:main`
- `ghcr.io/owner/repository:latest`
- `ghcr.io/owner/repository:sha-COMMIT_SHA`

Each publish creates a `github-packages` deployment record with a package link.
The job summary lists the tested commit, tags, image digest, and pull command.
Failed checks and pull requests never publish. This deployment records a package
release, not a rollout to your machine; pull/recreate it or use the Pi timer below.

The workflow uses GitHub's built-in `GITHUB_TOKEN`; no cloud key or application secret is required. After the first publish, change the package's visibility to **Public** so the Pi can pull without credentials. Protect `main` and workflow changes: a trusted image can read runtime credentials. The dashboard and frontend are built locally from the checkout.

## Pull a published image on Windows or Pi

Set this in `.env`, using lowercase owner/repository names:

```dotenv
DOBBY_IMAGE=ghcr.io/your_owner/your_repo:main
```

Then run:

```text
docker compose -f compose.yaml -f compose.registry.yaml pull bot
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build --pull never bot
```

The override only replaces the `bot` service's image; the other services are unchanged. Use both files for starts/recreates in registry mode so you do not switch back to local builds.

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

Every five minutes plus a small randomized delay, the timer runs `scripts/update-pi.sh` as root: it pulls first (a failed pull leaves the current container running), then recreates the `bot` service if its image changed. `flock` prevents overlapping runs. No automatic Git pull occurs, so Compose files, migrations, the dashboard and the frontend change only through a deliberate `git pull` and rebuild.

Updates briefly reconnect Discord and discard any pending Confirm previews. Only trusted administrators should be able to modify `/opt/dobby`, since the root timer reads its Compose files. Old images are not pruned automatically; check `docker system df` occasionally.

To stop updates:

```bash
sudo systemctl disable --now dobby-update.timer
sudo systemctl stop dobby-update.service
```

Disable the timer **before** stopping Dobby for maintenance; otherwise the next check starts it again.

## Rollback and configuration updates

Disable the timer, set `DOBBY_IMAGE` to a known-good `:sha-COMMIT_SHA` tag, then run the published-image pull/up commands. This rolls back bot code, not anything already published to a calendar or social account.

For Compose, migration, dashboard or frontend changes, run `git pull --ff-only`, then `docker compose up -d --build` (which re-runs `migrate`). Reinstall changed systemd units and `sudo systemctl daemon-reload`. Never copy `.env.example` over your existing `.env`.

## Live smoke test

1. Without an allowed role/user grant, try `/events` and a mention; nothing should happen beyond a refusal.
2. As an allowed user, `/help` lists Google Calendar, Notion, Instagram and LinkedIn.
3. `/events` returns the connected calendar's events; `@Dobby schedule a test sync tomorrow at 10am and invite <a registered teammate>` creates it with that person's calendar email.
4. `/notion search query:<a known page>` finds it.
5. `/linkedin post text:test` shows a preview; have someone else click Confirm (refused), then Cancel it yourself. Confirm one real post only when you mean it.
6. `/instagram posts` lists our posts; `/instagram story post:1` previews the story; Cancel.
7. From the second LAN machine, sign in with a local admin (when enabled), verify an incorrect password is rejected, sign out, and confirm protected pages require login. If OAuth is enabled, also check Google/Discord sign-in. On the dashboard, the audit log shows the tool calls above as `ok`, with no arguments or results stored.
8. Restart the bot; `bot_ready` appears and an old preview's buttons no longer work.
9. If updates are enabled, push a harmless change and verify publication plus the Pi update.

If hidden password confirmation repeatedly fails, run the local-admin command with `--no-confirm` to enter it once. Input remains hidden and length-checked. Use an interactive terminal without `-T`; the default confirmation flow allows three attempts and never saves mismatched input.

## Composio cannot write its cache in the read-only bot container

The Composio SDK writes downloaded tool files to a cache directory, created the first time a tool
returns a file attachment (import itself no longer touches the filesystem). The bot service sets
`COMPOSIO_CACHE_DIR=/tmp/.composio` so it uses the existing writable `/tmp` tmpfs while the root
filesystem remains read-only; the test service uses the same setting. The symptom is a tool call
failing with a `RuntimeError` naming `COMPOSIO_CACHE_DIR`, or a `pathlib.mkdir` permission error —
not a startup failure. Update `compose.yaml` and run
`docker compose up -d --no-deps --force-recreate bot`. No image rebuild is required for this
environment-only change, and `docker compose restart` does not apply it. The cache is temporary and
disappears when the container stops; service connections remain in Composio.
