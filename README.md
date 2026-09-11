# Dobby — Discord Meeting Scheduler

Run Dobby on a **Raspberry Pi or Windows computer using Docker**. Dobby turns Discord requests into Google Calendar meetings using Gemini, speaks in an eager house-elf voice, and asks you to confirm a preview in the requesting channel or thread before changing the calendar.

Everything runs on your own machine, so there is nothing to pay for hosting. That machine must stay powered on and connected to the internet. Gemini runs remotely; the Pi does not run an AI model locally.

## Contents

- [Features](#features)
- [Set up Dobby](#set-up-dobby)
- [How to use Dobby](#how-to-use-dobby)
- [Everyday Docker commands](#everyday-docker-commands)
- [Test it in Docker](#test-it-in-docker)
- [Automatic updates from GitHub](#automatic-updates-from-github)
- [Secrets and access](#secrets-and-access)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Features

- Mention `@Dobby` to create, rename, reschedule, change descriptions/locations, or delete meetings. Deletes and changes find the meeting by title, so no event ID is needed.
- Invite people by name: Dobby asks for an email once, remembers it in `data/contacts.json`, and sends Google invitations.
- Ask `@Dobby what can you do?` for a summary; off-topic messages get a short Gemini-written reply in Dobby's voice that ends with Dobby leaving.
- Mention requests always use up to twelve recent channel messages, plus the channel and thread names, so Dobby can propose a title from the discussion instead of asking.
- New meetings default to **one hour** unless you specify otherwise.
- Structured previews in the requesting channel or thread, confirmed with a 🟢 or 🔴 reaction by the requester only.
- Server, role/user, and channel restrictions, checked again at confirmation.
- Conflict detection, exact event selection, and protection against overwriting newer edits.
- Read-only credential mounts and one small writable `data/` folder; no secrets in images or your public repository.
- Docker restart policy, bounded logs, and optional automatic Pi updates from tested GitHub images.

Design: [ARCHITECTURE.md](ARCHITECTURE.md). Pi deployment and updates: [DEPLOYMENT.md](docs/DEPLOYMENT.md). Sources: [RESEARCH.md](docs/RESEARCH.md).

## Set up Dobby

Six steps, in order. Steps 1–4 gather credentials, step 5 verifies them, step 6 starts Dobby. Run **every** command from the folder you cloned into.

| Step | What it does | Needs a browser? |
| --- | --- | --- |
| 1 | Install Docker, create `.env`, `secrets/` and `data/` | No |
| 2 | Create the Discord bot, fill in IDs | Yes |
| 3 | Get a Gemini API key | Yes |
| 4 | Link the Google account | Yes |
| 5 | Check the configuration | No |
| 6 | Start Dobby | No |

### Step 1: Install Docker and get the code

| Machine | Requirements |
| --- | --- |
| Windows | Docker Desktop using **Linux containers**, normally with its WSL 2 backend. Start Docker Desktop before running commands. |
| Raspberry Pi | Pi 4/5 recommended, **64-bit Raspberry Pi OS**, Docker Engine and the Compose plugin. Published images support `linux/arm64`; 32-bit Pi OS is not supported. |

Follow the official [Windows Docker Desktop installation](https://docs.docker.com/desktop/setup/install/windows-install/) or [Docker Engine Debian installation for 64-bit Pi OS](https://docs.docker.com/engine/install/debian/). On Pi, `uname -m` should report `aarch64`. See the [Pi guide](docs/DEPLOYMENT.md) for permissions and startup.

Clone your repository and open a terminal in its folder. You do **not** need host Python when using Docker.

Create your configuration file, secrets folder and data folder now.

Windows PowerShell:

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force secrets, data
docker compose version
Get-Item .env, secrets, data | Select-Object Name, Mode
```

Pi/Linux:

```bash
cp .env.example .env
mkdir -p secrets data
chmod 700 secrets data
chmod 600 .env
docker compose version
ls -ld .env secrets data
id -u
id -g
```

**Expected result:** `.env` is a **file**; `secrets` and `data` are empty **directories**. On Windows, `Mode` reads `-a---` for `.env` and `d----` for the directories. `data/` holds Dobby's contact memory and must stay writable by the container user.

> ⚠️ **Do not skip this.** If `.env` or `secrets/google-token.json` is missing when you run any `docker compose` command, Docker creates a **directory** with that name and mounts it over Dobby's credential paths. Step 5 reports this clearly. To fix it, delete the stray directory and repeat this step.

On Pi/Linux, edit `.env` and set `DOBBY_UID` and `DOBBY_GID` to the two IDs that `id -u` and `id -g` printed. This lets the non-root container read your private files, write `data/contacts.json`, and create the OAuth token as your user. Windows keeps the defaults.

### Step 2: Create and invite Dobby in Discord

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application named **Dobby**. Set the bot username or server nickname to **Dobby**.
2. Obtain its bot token and put it in local `.env` as `DISCORD_TOKEN`.
3. Enable **Message Content Intent** — it is required for mention and context requests. No Presence or Server Members privileged intent is needed. Leave Interactions Endpoint URL empty.
4. Invite with `bot` and `applications.commands` OAuth scopes. Grant **View Channels**, **Send Messages**, **Send Messages in Threads**, **Read Message History**, **Add Reactions**, and **Attach Files** (for `/events`) in scheduling channels. **Manage Messages** is optional and only lets Dobby clear the 🟢/🔴 reactions after you choose. Do not grant Administrator or Manage Roles.
5. Enable Discord Developer Mode and copy the server, scheduler role, and scheduling text channel IDs into `.env`:

```dotenv
DISCORD_GUILD_ID=YOUR_SERVER_ID
ALLOWED_ROLE_IDS=YOUR_SCHEDULER_ROLE_ID
ALLOWED_USER_IDS=
ALLOWED_CHANNEL_IDS=YOUR_SCHEDULING_CHANNEL_ID
MENTION_CHANNEL_IDS=YOUR_SCHEDULING_CHANNEL_ID
TEAM_TIMEZONE=America/Denver
```

Replace placeholders with numeric IDs. Lists accept comma-separated IDs. `ALLOWED_USER_IDS` optionally grants access to specific users instead of requiring a role. At least one user or role must be allowed; there is no administrator bypass.

Leave `MENTION_CHANNEL_IDS` **empty to let Dobby answer mentions in any channel it can see** in that server; list IDs to restrict mentions to those channels. Either way, mentions must also satisfy `ALLOWED_CHANNEL_IDS` when it is set, and the user/role allowlist always applies. Threads and forum posts use their own IDs in these allowlists, not the parent channel ID. Private thread access is rechecked before confirmation. Tell members that mention requests send recent channel text, author display names and the channel or thread name to Gemini.

### Step 3: Get a Gemini API key

Create a key in [Google AI Studio](https://aistudio.google.com/apikey), then set `GEMINI_API_KEY` in `.env`. The default model is `gemini-2.5-flash-lite`; another structured-output model can be selected with `GEMINI_MODEL`.

Use a Gemini free-tier project if you want requests limited by its free quota rather than billed. Models and quotas can change. Free-tier content may improve Google's products, so avoid confidential meeting content on that tier. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

### Step 4: Link the Google account using Docker

Use a dedicated Google account with access only to the intended team calendar for strongest isolation.

1. Create/select a Google API project and enable the **Google Calendar API**.
2. Configure Google Auth Platform / OAuth consent. Add the team account as a test user when the app is in Testing. Request `https://www.googleapis.com/auth/calendar.events`.
3. Create a **Desktop app** OAuth client. Download its JSON to `secrets/google-client.json`.
4. On Windows (or a Pi with a local browser), run:

```text
docker compose -f compose.auth.yaml build
docker compose -f compose.auth.yaml run --rm --service-ports --no-deps link-google
```

Open the printed Google authorization link in a browser **on the same computer**. Sign in to the intended account and approve access. The callback is published only on `127.0.0.1:8765`. The helper saves `secrets/google-token.json` and exits. Complete authorization within five minutes. No API key or refresh token is printed.

For a headless Pi, complete this on Windows and securely copy the token to the Pi; see [transfer instructions](docs/DEPLOYMENT.md#move-from-windows-to-the-pi). The normal running bot has no listening ports.

Set `GOOGLE_CALENDAR_ID=primary` in `.env`, or use the calendar ID from Google Calendar settings → Integrate calendar. The linked account must be able to modify it.

Testing-mode OAuth refresh tokens generally expire after seven days for this scope. Relink when needed, or configure appropriate production/internal consent. Publishing an OAuth app does not make the calendar public. [Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

### Step 5: Check your configuration

Confirm every setting before starting Dobby. This contacts nothing and changes nothing:

```text
docker compose run --rm --no-deps dobby python -m bot.main --check
```

**Success** prints one line and exits `0`:

```text
2026-05-01 12:00:00,000 scheduler config_ok guild=123456789 timezone=America/Denver model=gemini-2.5-flash-lite mention_channels=1
```

**Any problem** exits `2` and names exactly what to fix:

| Message | Fix |
| --- | --- |
| `Missing configuration: DISCORD_TOKEN, …` | Fill the named keys in `.env` (steps 2–3) |
| `DISCORD_GUILD_ID must be the numeric guild ID.` | Use the numeric ID, not the server name |
| `Set ALLOWED_USER_IDS or ALLOWED_ROLE_IDS; …` | Grant at least one user or role (step 2) |
| `TEAM_TIMEZONE is not a known IANA zone: …` | Use a name like `America/Denver` |
| `Google token file not found at …` | Finish the account linking in step 4 |
| `… is a directory, not a file. …` | Delete that directory, then redo step 1 and step 4 |
| `Google token file at … is missing: refresh_token` | Link the account again (step 4) |

Repeat this step until it prints `config_ok`.

### Step 6: Start Dobby

Use the **same commands on Windows and Pi**:

```text
docker compose up -d --build
docker compose logs --tail 50 -f dobby
```

**Expected result:** the log shows `scheduler bot_ready guild=…`. Dobby's slash commands appear in your server within a few seconds. Press Ctrl+C to stop watching logs; Dobby keeps running in the background.

If the log instead repeats `startup_failed`, stop with `docker compose down` and rerun step 5 — the restart policy retries a misconfigured container indefinitely.

`.env` and the OAuth token are read-only runtime mounts and `data/` is a writable bind mount, so all three survive removing and rebuilding the container. After changing either, apply it with `docker compose up -d --force-recreate --no-build`.

Run **one instance per Discord token**. Stop Windows Dobby with `docker compose down` before starting the Pi copy. To run both at once for testing, use a separate Discord application/token and a test calendar.

## How to use Dobby

### Before your first request

Ask for the scheduler role and use a channel or thread Dobby is allowed in. Type `@Dobby` and **select the bot from Discord's mention suggestions**. Plain text resembling a mention will not trigger it.

### Create a meeting

```text
@Dobby schedule a Planning meeting tomorrow at 10am
@Dobby schedule a Planning meeting tomorrow at 10am for 45 minutes
```

The first request defaults to one hour. Times use the team timezone unless you specify another. Dobby replies with a preview (bold **Title**, **When**, **Location**, **Invitees** lines) and puts 🟢 and 🔴 reactions on it: react 🟢 to save or 🔴 to discard. Nothing is written before you confirm. Every event is titled `<place> | <event name>`, where the place is the thread (or channel) name when it is one word and a one- or two-word label Gemini derives from it otherwise, so titles stay short. The confirmation names the event and its month/day instead of an ID. If a date or time is missing, send a new complete request. Dobby only keeps a conversation going for its own questions (a missing email, the meeting title, or which of several matches you meant), and only for five minutes. Dobby never sends DMs.

### Turn a discussion into a meeting

```text
Teammate: Let's do the release review next Tuesday at 2pm.
Teammate: We need 30 minutes; the agenda is the launch checklist.
You: @Dobby make this a meeting
```

Every mention request sends up to twelve preceding messages from that channel or thread, with author display names, plus the channel and thread names. Dobby uses them to fill in a missing title before asking you for one. Bot messages are excluded, text is capped at 1,500 characters each, and requesters without Read Message History get no context. No attachments, links, other channels, or archives are fetched. Slash commands send no channel context. Review the extracted details before confirming.

### Ask Dobby a question

`@Dobby what can you do?` gets a short list of everything above with examples. Any other message that is not about the calendar gets a brief answer in Dobby's voice, generated by Gemini, ending with Dobby announcing that it is leaving; that reply closes the exchange and never touches the calendar. This lives in `bot/chat.py`, separate from the scheduling code.

### Modify or delete a meeting

Name the meeting and Dobby finds it in the next 60 days of the calendar:

```text
@Dobby move the release review to tomorrow at 3pm
@Dobby delete the design review
@Dobby delete that meeting          (title taken from the recent discussion)
```

A single clear match comes back as a preview asking "is this the right meeting?", so 🟢 confirms both the match and the change. If nothing names the meeting, Dobby asks for the title; if several look alike, it lists up to three and you reply with the number. Update previews and confirmations list every change as `old → new`. Moving an event preserves its duration unless specified otherwise, and existing guests receive Google notifications. For an exact selection, run `/events days:30`, copy the ID, and add `event_id:PASTE_EVENT_ID` to the request.

### Invite people

```text
@Dobby set up a design review Friday at 2pm and invite Maya and Leonard
```

Dobby looks each name up in its contact memory. For anyone unknown it asks in the channel; reply to that question with `Maya: maya@example.com` (or just the address when one name is missing) and Dobby saves it and continues the request. `/contacts action:add name:Maya email:maya@example.com` teaches Dobby ahead of time, `action:list` shows names with masked addresses, and `action:remove` forgets one. Contacts are shared by all allowed users and stored in `data/contacts.json`. Invitees receive Google Calendar invitations when you confirm.

### Slash command reference

| Command | Purpose |
| --- | --- |
| `/schedule request:Create Planning tomorrow at 10am` | Preview a new meeting |
| `/events days:30` | Upcoming events and IDs visible in the channel |
| `/schedule request:Move this meeting to tomorrow at 3pm event_id:ID` | Reschedule |
| `/schedule request:Rename this meeting to Review event_id:ID` | Rename |
| `/schedule request:Delete this meeting event_id:ID` | Preview deletion |
| `/contacts action:add name:Maya email:maya@example.com` | Save, list or remove invitee emails (`add`, `list`, `remove`) |
| `/calendar_help` | Examples and privacy information |

Mention and slash-command previews, event lists, and results are visible to everyone with access to the requesting channel or thread. Dobby does not send DMs. Only the requester can confirm or cancel a proposal. Previews expire after two minutes and Dobby's questions after five. Restarts/updates invalidate both. After an uncertain network failure, check `/events` before retrying because the write may already have completed.

## Everyday Docker commands

| Task | Command |
| --- | --- |
| Start/build | `docker compose up -d --build` |
| Status | `docker compose ps` |
| Follow logs | `docker compose logs --tail 50 -f dobby` |
| Stop/remove container, keep secrets | `docker compose down` |
| Apply changed credentials/config | `docker compose up -d --force-recreate --no-build` |
| Apply source updates | `git pull --ff-only`, then `docker compose up -d --build` |
| Restart without rebuilding | `docker compose restart dobby` |
| Rebuild from scratch (dependency or Dockerfile changes) | `docker compose build --no-cache`, then `docker compose up -d` |
| Check config without starting | `docker compose run --rm --no-deps dobby python -m bot.main --check` |
| Check config of the running container | `docker compose exec dobby python -m bot.main --check` |
| Show the effective Compose configuration | `docker compose config` |
| Recent logs only | `docker compose logs --since 1h dobby` |
| CPU/memory use | `docker stats --no-stream $(docker compose ps -q)` |
| Open a shell inside the container | `docker compose exec dobby sh` |
| View saved contacts | `docker compose exec dobby cat /data/contacts.json` |
| Back up contact memory | copy `data/contacts.json` somewhere safe; restore by copying it back |
| Switch to the published image (Pi) | `docker compose -f compose.yaml -f compose.registry.yaml pull dobby`, then `... up -d --no-build --pull never dobby` |
| Remove container and local image | `docker compose down --rmi local` |
| Reclaim disk from old images | `docker image prune -f` |
| Run the test suite in Docker | `docker compose -f compose.test.yaml run --rm tests` |
| Run lint/format checks in Docker | `docker compose -f compose.test.yaml run --rm lint` |

Pi restarts Dobby when Docker starts after boot unless explicitly stopped. Windows Docker Desktop must remain running; sleep/shutdown interrupts hosting. Disable the optional Pi update timer before intentionally stopping Dobby, otherwise it would start Dobby again.

## Test it in Docker

Everything below runs in containers; no host Python is required.

**Test suite and linting.** These need no Discord, Gemini or Google credentials and run with networking disabled:

```text
docker compose -f compose.test.yaml run --rm tests
docker compose -f compose.test.yaml run --rm lint
```

**Configuration preflight.** Once `.env` and `secrets/google-token.json` exist, validate them without connecting to Discord:

```text
docker compose run --rm --no-deps dobby python -m bot.main --check
```

It prints `config_ok` and exits `0` when the settings and token file are usable. Any problem exits `2` and names the setting to fix, for example `Missing configuration: DISCORD_TOKEN` or `TEAM_TIMEZONE is not a known IANA zone`. Run this before `docker compose up`: the runtime restart policy otherwise retries a misconfigured container indefinitely.

Create `.env` and `secrets/` before the first `up`. Docker creates a **directory** where a missing secret file was expected, which then mounts over the runtime paths; the preflight reports this explicitly. If you have host Python, `python scripts/bootstrap.py` creates `.env`, `secrets/` and `data/` correctly and reports the Pi UID/GID to set.

## Automatic updates from GitHub

Pushes to `main` run checks and publish `linux/amd64` and `linux/arm64` images to GitHub Container Registry. GitHub uses its built-in `GITHUB_TOKEN`; do not upload bot credentials. Make the resulting package public for unauthenticated pulls.

The optional Pi timer checks every five minutes and recreates the container when its image changes. See [automatic updates](docs/DEPLOYMENT.md#automatic-updates-on-the-pi). Windows can manually pull the same image. A Git push alone does not update a machine until this pull setup is enabled.

## Secrets and access

- Credentials belong in local `.env` and `secrets/`, ignored by Git and Docker builds. Only `.env.example` belongs in Git.
- Compose mounts `.env` and `google-token.json` into the runtime read-only, plus `./data` read-write for `contacts.json`. The OAuth client file is only needed for linking.
- `data/contacts.json` holds teammates' email addresses in plain text; keep it out of Git (it is ignored) and treat the folder like `secrets/`.
- On Pi use mode `600` for credential files and `700` for `secrets/`, owned by the configured container UID/GID. On Windows restrict the folder to your account.
- Compose file secrets are **not encrypted at rest**. Host/Docker administrators and deployed code can read them; protect the device and main branch.
- Authorized users can manage all supported events on the configured calendar. OAuth can reach other calendars available to the linked account; use a dedicated account.
- Dobby never grants calendar sharing or returns Google credentials to users. Restrict who can assign the scheduler role.
- Rotate leaked credentials immediately. Removing a file does not remove it from Git history. Enable GitHub push protection where available.

## Limitations

- One active instance, one Discord server, one configured Google calendar.
- Single future timed events up to 24 hours; no recurring/all-day event edits.
- Google Calendar entries, not Discord Scheduled Events. Invitations work by name once Dobby has learned the person's email; existing attendees are preserved and merged. No Meet creation or sharing.
- Conflicts check the linked calendar only, not individual private availability. Another app can write an overlap between the check and insert.
- Gemini quotas and data terms still apply. Docker does not make paid API usage free.
- Continuous outbound internet is needed. No router port forwarding is required.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Cannot connect to Docker | Start Docker Desktop/Engine; use Linux containers |
| Permission denied reading secrets | Match Pi UID/GID to file owner; allow Windows Docker access to project folder |
| Token mount missing | Complete linking first; do not create an empty token file |
| OAuth callback fails | Browser must be on helper's host; port 8765 must be free; rerun after timeout |
| Repeated restarts | Run `docker compose down`, then step 5's `--check`; it names the setting to fix |
| `.env` or token is a directory | Docker created it because the file was missing; delete it, redo steps 1 and 4 |
| Bot starts but commands missing | Confirm `DISCORD_GUILD_ID`; commands sync to that one server on `bot_ready` |
| Gateway 4014 | Enable Message Content Intent in the developer portal; it is always required |
| Mention ignored | Actual mention, allowed role, correct text channel/server, ten-second cooldown |
| Channel preview unavailable | Grant Send Messages, Send Messages in Threads, and Add Reactions in the requesting channel |
| Context inaccessible | Bot and requester need View Channel and Read Message History |
| Reactions do nothing | Only the requester within two minutes; bot needs Add Reactions; look for `mention_access_denied` in the logs |
| Contacts not remembered, or Dobby goes quiet after you send an email | `data/` must exist and be writable by `DOBBY_UID` (a root-owned folder Docker created is the usual cause). `mkdir -p data`, `chown` it to your UID/GID, recreate the container; step 5's `--check` now tests this |
| Google auth expires | Relink Testing-mode OAuth and recreate container |
| Gemini fails | Check key, model and quota; there is no paid fallback |
| Old preview fails | Restart/update, expiry, permission change or stale event; request another preview |
| Registry pull denied | Make package public and check lowercase `DOBBY_IMAGE` |

## Development

Prefer [Test it in Docker](#test-it-in-docker) to run the suite in the image it ships in. Optional host development still works with Python 3.14:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\ruff check bot scripts tests
.\.venv\Scripts\ruff format --check bot scripts tests
.\.venv\Scripts\python -m bot.main
```

On Pi/Linux use `.venv/bin/python`. Stop Docker Dobby before launching a host copy with the same token. Dependencies are pinned in `requirements.txt`; direct ranges are in `requirements.in`. Tests mock external APIs; live testing requires your credentials. Dobby's phrasings live in `bot/responses/*.txt` (20 lines per file); edit them freely, the suite checks every file still has 20 usable lines.
