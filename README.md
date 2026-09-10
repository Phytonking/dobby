# Dobby — Discord Meeting Scheduler

**Dobby** turns Discord meeting requests into Google Calendar events using Gemini. Authorized teammates can mention **@Dobby** or use slash commands. Dobby prepares a private preview; clicking **Confirm calendar change** creates, updates, or deletes the event.

## Contents

- [Features](#features)
- [Set up Dobby](#set-up-dobby) — for the person installing the bot
- [How to use Dobby](#how-to-use-dobby) — for teammates scheduling meetings
- [Scope and limitations](#scope-and-limitations)
- [Deploy automatically to Google Cloud Run](#deploy-automatically-to-google-cloud-run)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

## Features

- Dobby speaks in an eager, polite house-elf voice, with clear meeting details and confirmation buttons.
- Schedule meetings in plain language, with a **one-hour default duration** unless you specify otherwise.
- Turn the last few messages of a discussion into a meeting.
- Create, rename, reschedule, change descriptions/locations, and delete single timed meetings.
- Exact event IDs for changes/deletions, calendar conflict checks, and version checks to prevent overwriting someone else's edit.
- Server, user/role, and optional channel allowlists. No administrator bypass.
- Slash replies are ephemeral; mention previews arrive by DM. No calendar details are posted publicly by the bot.
- Secrets excluded from Git and Docker builds. Cloud secrets live in Google Secret Manager.
- Tests and automatic Cloud Run worker-pool deployment on pushes to `main`, using GitHub OIDC instead of a Google service-account key.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design, [deployment](docs/DEPLOYMENT.md) for cloud setup, and [research](docs/RESEARCH.md) for official API references.

## Set up Dobby

### What you need

- Python 3.14 installed on the computer running the bot locally.
- Permission to add a bot and configure roles/channels in your Discord server.
- A Gemini API key from Google AI Studio.
- A dedicated Google account and team calendar, plus access to configure a Google Cloud OAuth client.
- For cloud hosting later: a GitHub repository and a billing-enabled Google Cloud project.

### Step 1: Install the project

Use Python 3.14. Run commands from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
New-Item -ItemType Directory -Force secrets
```

On macOS/Linux use `.venv/bin/python` instead. Edit `.env` locally; do not paste secrets into Discord, issue reports, or GitHub variables.

### Step 2: Create and invite Dobby

1. Open the [Discord Developer Portal](https://discord.com/developers/applications), create an application named **Dobby**, and obtain its bot token. Put it in `.env` as `DISCORD_TOKEN`. Set the bot's username or server nickname to **Dobby** so teammates can find it by that name in Discord's mention picker.
2. On the Bot page enable **Message Content Intent** for mentions with recent-message context. Presence and Server Members intents are not needed. Leave the Interactions Endpoint URL empty: this bot uses the Gateway.
3. Invite it using OAuth2 URL Generator with `bot` and `applications.commands` scopes. Grant **View Channels**, **Send Messages**, **Read Message History**, and **Attach Files** only in the scheduling channels. Do not grant Administrator or Manage Roles. Disable Public Bot if you do not want other people inviting it.
4. Turn on Discord Developer Mode, then copy the server ID, scheduler role ID, and scheduling text channel ID. Configure:

```dotenv
DISCORD_GUILD_ID=YOUR_SERVER_ID
ALLOWED_ROLE_IDS=YOUR_SCHEDULER_ROLE_ID
ALLOWED_USER_IDS=
ALLOWED_CHANNEL_IDS=YOUR_SCHEDULING_CHANNEL_ID
MENTION_CHANNEL_IDS=YOUR_SCHEDULING_CHANNEL_ID
TEAM_TIMEZONE=America/Denver
```

Values must be numeric Discord IDs, with comma-separated lists for multiple IDs. Replace the examples; do not leave the words `YOUR_...`. `ALLOWED_USER_IDS` is an optional explicit alternative to the role allowlist. Leaving both user and role lists empty prevents startup.

Mention channels must also satisfy `ALLOWED_CHANNEL_IDS` if that list is set. Empty `MENTION_CHANNEL_IDS` disables mention handling and the Message Content intent, while slash commands still work. This version supports ordinary server text channels; threads are not supported for context requests. Tell people in those channels that invoking context scheduling sends recent text to Gemini. Restrict who can assign the scheduler role.

### Step 3: Configure Gemini

Create an API key in [Google AI Studio](https://aistudio.google.com/apikey). Put it in `.env` as `GEMINI_API_KEY`. The default model is `gemini-2.5-flash-lite`; `GEMINI_MODEL` can select another model supporting structured output.

Google currently lists a free tier for this model, with project-specific quotas. Check AI Studio before use; do not assume unlimited free requests or that a billing-enabled project stays on the free tier. Use a separate unbilled Gemini project if you want to keep it separate from the billed Cloud Run project. Free-tier inputs/outputs may be used to improve Google's products. Avoid confidential meeting text on that tier. [Pricing and data treatment](https://ai.google.dev/gemini-api/docs/pricing).

### Step 4: Link Google Calendar

For strongest isolation, use a **dedicated Google account that only owns or can access the team calendar**. The OAuth events scope is not restricted by Google to one calendar; the bot enforces one configured calendar, but a stolen OAuth refresh token could access other calendars available to that account. Do not link a personal account containing unrelated sensitive calendars.

1. Create/select a Google Cloud project and enable **Google Calendar API**.
2. Configure Google Auth Platform / OAuth consent. For testing, add the dedicated Google account as a test user. Request only `https://www.googleapis.com/auth/calendar.events`.
3. Create an OAuth client of type **Desktop app**. Download its JSON to `secrets/google-client.json`.
4. Run the local authorization helper. It opens the browser, verifies the OAuth state, and receives the callback on localhost:

```powershell
.\.venv\Scripts\python scripts/link_google.py
```

Sign in to the intended team account. The helper saves `secrets/google-token.json` without displaying credentials. This file contains the refresh token and client credentials. It is plaintext locally: protect your Windows account and restrict the `secrets` folder to your account. On Unix newly created token files use mode `0600`.

Set `GOOGLE_CALENDAR_ID=primary`, or copy a specific calendar's ID from Calendar settings → Integrate calendar. The linked account must have permission to modify events there. The bot never shares the calendar or grants Google-account access to Discord users.

External OAuth apps in **Testing** generally receive refresh tokens that expire after seven days for this scope. Re-run the helper after expiry, or properly configure the consent app for production/internal use as applicable. Publishing may require verification depending on your app/users; it does not make the calendar public. [Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

### Step 5: Start Dobby

```powershell
.\.venv\Scripts\python -m bot.main
```

Wait for `bot_ready` in the console. Slash commands register in the configured server at startup. Keep your computer running for local testing. Stop the local instance before running the same Discord token in Cloud Run.

Continue with the usage examples below to create your first test meeting. For an always-running bot and automatic updates on push, follow [cloud deployment](#deploy-automatically-to-google-cloud-run).

## How to use Dobby

### Before your first request

1. Ask your server administrator for the configured scheduler role.
2. Use the server's designated scheduling text channel.
3. Enable DMs from server members so Dobby can send you private previews. Alternatively, use `/schedule` for a private reply inside the server.

**To mention Dobby, type `@Dobby` and select the bot from Discord's suggestions.** Plain text that looks like a mention will not trigger it. The bot recognizes its Discord ID, so changing its display name later does not require a code change.

### Create a meeting

Give Dobby a meeting title, date, and start time:

```text
@Dobby schedule a Planning meeting tomorrow at 10am
@Dobby schedule a Planning meeting tomorrow at 10am for 45 minutes
```

The first request creates a **one-hour** preview; the second requests **45 minutes**. Times use the configured team timezone unless you specify another timezone.

Open Dobby's DM, review the exact title, date, start and end time, then click **Confirm calendar change**. Click **Cancel** to discard it. Nothing is written to Google Calendar until you confirm.

If Dobby asks for missing details, send a new complete request in the scheduling channel. It does not keep an ongoing DM conversation.

### Turn a discussion into a meeting

After discussing the meeting in the scheduling channel, mention Dobby:

```text
Teammate: Let's do the release review next Tuesday at 2pm.
Teammate: We need 30 minutes; agenda is the launch checklist.
You: @Dobby make this a meeting
```

Context is read only when the request refers to `this`, `that`, `above`, `discussion`, `context`, or `conversation`. Up to six preceding messages are inspected; bot messages are excluded and each text is capped at 1,500 characters. No other channels, attachments, linked pages, or message archives are fetched. Missing or ambiguous details cause a clarification instead of a write.

### Find, modify, or delete a meeting

1. Run `/events days:30` to get a private list of upcoming meetings.
2. Copy the exact ID of the meeting you want to change.
3. Use that ID in a slash command's `event_id` option, or include `event_id:ID` in a mention:

```text
@Dobby move this meeting to tomorrow at 3pm event_id:PASTE_EVENT_ID
@Dobby rename this meeting to Release Review event_id:PASTE_EVENT_ID
@Dobby delete this meeting event_id:PASTE_EVENT_ID
```

Replace `PASTE_EVENT_ID` with the copied ID. Review the private preview and confirm. Moving a meeting preserves its existing duration unless you request a different one. Existing invitees receive Google notifications when the event changes or is deleted.

### Slash command reference

| Command | Use |
| --- | --- |
| `/schedule request:Create Planning tomorrow at 10am` | Preview a new one-hour meeting |
| `/events days:30` | Privately list upcoming events and exact IDs |
| `/schedule request:Move this meeting to tomorrow at 3pm event_id:ID` | Reschedule the selected event, preserving its duration |
| `/schedule request:Rename this meeting to Release Review event_id:ID` | Change its title |
| `/schedule request:Delete this meeting event_id:ID` | Preview deletion |
| `/calendar_help` | Examples and privacy information |

Mentions also support updates/deletes with an explicit `event_id:ID` in the message. Prefer slash commands when you want the request itself to be private; a mention message is visible to everyone who can read its channel.

Confirmations expire after two minutes and are single-use. A restart/deploy invalidates pending previews. If a network error occurs after confirming, inspect `/events` before submitting a new request: Google may already have completed the write.

## Scope and limitations

- One Google account, one configured calendar, one Discord server, and one running bot instance.
- Authorized Discord members can manage **all supported events on that calendar**, including events created outside the bot. Use a dedicated team calendar and carefully choose the scheduler role.
- This version creates calendar entries, not Discord Scheduled Events. Team members see entries through their existing Google Calendar access. It does not automatically map Discord users to email addresses, invite new guests, create Meet links, or grant calendar sharing permissions. Existing attendees are preserved and receive Google notifications for edits/deletions.
- Single future timed events only, at most 24 hours long. No recurring/all-day event edits. Existing duration is preserved when moving an event; one hour is the default for new events only.
- Conflict detection checks the linked calendar, including all-day busy events, not each teammate's private availability. It blocks overlaps rather than choosing a replacement time. An external writer can still insert an event between the final check and write because Calendar has no atomic “insert only if free” operation.
- AI parsing can be wrong. The exact private preview and confirmation are intentional; natural-language text never directly executes arbitrary API calls.
- Cloud Run workers are billed while idle. The free Gemini tier does not make hosting free.

## Deploy automatically to Google Cloud Run

Complete the one-time [Google Cloud and GitHub setup](docs/DEPLOYMENT.md). Then each push to `main` runs the secret guard, lint, tests, builds the container, pushes a commit-tagged image, and deploys one Cloud Run worker. Pull requests run checks without cloud authentication. No secret values belong in the repository or GitHub Actions variables.

This folder may initially be an uninitialized Git directory. Create your GitHub repository, initialize Git if needed, review files, and push:

```powershell
git init -b main
git add .
.\.venv\Scripts\python scripts/check_secrets.py
git diff --cached --stat
git commit -m "Add Discord Gemini Calendar scheduler"
git remote add origin https://github.com/YOUR_OWNER/YOUR_REPO.git
git push -u origin main
```

Never force-add `.env` or `secrets/`. Enable GitHub secret scanning/push protection where available. The included scanner is a baseline guard, not a guarantee against every credential format. If a secret is ever committed, revoke/rotate it immediately; removing the file does not remove it from history.

## Development

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\ruff check bot scripts tests
.\.venv\Scripts\ruff format --check bot scripts tests
```

Dependencies are pinned in `requirements.txt`; `requirements.in` documents the direct dependency ranges. The pinned file includes test tools so CI and local verification share versions.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Bot fails to start | Required `.env` entries, numeric IDs, IANA timezone, token file, correct Google account |
| Gateway closes with 4014 | Enable Message Content Intent or clear `MENTION_CHANNEL_IDS` |
| Mentions ignored | Correct role/user, server/channel IDs, actual bot mention, ordinary text channel, ten-second cooldown |
| No context / Forbidden | Bot and requester both need View Channel and Read Message History |
| No DM preview | Enable DMs or use `/schedule`; the bot does not publish private previews as a fallback |
| Google HTTP 403 | Calendar API enabled, writable calendar, OAuth scope, account access/quota |
| Google authorization fails after a week | Testing-mode refresh token expired; relink and rotate the cloud secret |
| Gemini request fails | Check model availability and AI Studio quotas/key; no automatic paid fallback |
| Old preview fails | It expired, bot restarted, access changed, or someone edited the event; create a new preview |

Logs contain operation type and Discord actor/server IDs, not prompts, event content, tokens, or raw API error bodies. Restrict Cloud Logging access because Discord IDs are still identifiers. Live Discord/Google testing requires your credentials and is not performed by the offline test suite.
