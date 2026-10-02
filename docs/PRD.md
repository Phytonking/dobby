# Dobby v2 — Product Requirements Document

## Overview

Dobby is a Discord bot for a UW student group that acts as an AI agent wired to real tools (Google Calendar, GitHub, Notion) via Composio. Users interact through Discord mentions and slash commands. Admins manage connections, users, and bot settings through a web dashboard.

## Goals

- Replace one-shot Gemini planning with a multi-step ReAct agent loop capable of using multiple tools per request
- Handle Google Calendar, GitHub, and Notion operations through a single Composio integration layer
- Provide a web dashboard for admins to manage user access, account connections, and guild settings
- Restrict dashboard access to pre-registered members from any email domain
- Persist conversation memory and agent actions in Postgres

---

## User Roles

| Role | Description |
|------|-------------|
| `admin` | Full dashboard access. Can pre-register users, connect integrations, modify guild settings, view audit log. Created by OAuth bootstrap, the local-admin CLI, or promoted by an existing admin. |
| `student` | Dashboard access after admin pre-registration. Can connect their own integrations. Cannot manage other users or guild settings. |

With OAuth enabled, `BOOTSTRAP_ADMIN_EMAIL` creates/promotes the matching user only when no admin exists. Local admins are created/reset with `python -m dashboard.local_admin USERNAME`; `--email` attaches credentials to an existing Google user. Existing admins may promote users in the dashboard.

---

## Authentication

### Web Dashboard

`AUTH_MODE` selects `oauth` (default), `local`, or `both`. All enabled login methods use the same `users` and `sessions` tables and role checks. No public signup is available. Switching modes disables the excluded login routes and existing sessions.

**Google OAuth**
- Scope: `openid email profile`
- No hosted-domain restriction; Google accounts from any domain are accepted
- Backend requires a verified Google email
- Backend additionally checks `users.uw_email` — must be pre-registered

**Discord OAuth**
- Scope: `identify email`
- Backend checks `users.discord_id` — must be pre-registered
- An admin registers the Discord ID on the same user row to allow both OAuth providers

**Local username/password**
- Available in `local` or `both`; requires loopback/private-network clients at login and on each authenticated request
- Local mode restricts the entire API to those networks; use a LAN firewall because Docker/proxies can mask the client IP
- Usernames: 3-64 letters, digits, dots, underscores or hyphens; normalized lowercase
- Passwords: 12-256 characters, prompted by CLI, stored only as salted scrypt hashes
- Invalid credentials return 401; five attempts per source IP per minute, then 429 (single-process limiter, reset on restart)
- CLI password reset revokes existing sessions; no browser password reset
- Local login returns 200 and sets the session cookie; frontend navigates to the dashboard

**Session**
- httpOnly cookie containing a signed session token (stored in `sessions` table)
- Fixed 7-day expiry from login (not refreshed on each request)
- Logout deletes the session row, clears the cookie, and returns 204; the frontend navigates to `/login`

### Discord Bot Access

- Existing role/channel/user allowlist in `guild_settings`
- Admin Discord role ID gates `/admin_*` slash commands
- Regular student role ID gates standard slash commands

---

## Agent Capabilities

### Architecture

The bot uses a Gemini ReAct loop:

```
User message
  → gather context (Postgres conversation history, channel info)
  → Gemini generates: text response OR tool call
  → if tool call: Composio executes → result appended
  → loop until text response
  → store turn in conversation_history
  → reply to Discord
```

Max tool calls per turn: 8 (prevents runaway loops).
Timeout: 60 seconds total per request.
Executor: single ThreadPoolExecutor worker (serializes all API calls).

### Tools (Phase 1)

All tools provided by Composio. Gemini sees them as function declarations.

**Google Calendar**
- Create event
- Update event
- Delete event
- List upcoming events
- Check availability

**GitHub**
- Create issue
- Update issue
- List issues
- Create PR comment
- Get PR / issue details

**Notion**
- Create page
- Update page
- Search pages
- Append block to page
- Get page content

### Slash Commands

| Command | Access | Description |
|---------|--------|-------------|
| `/ask <prompt>` | all allowed users | General agent request — any tool |
| `/schedule <details>` | all allowed users | Calendar shortcut — hints agent toward calendar tools |
| `/events` | all allowed users | List upcoming calendar events |
| `/issue <title> <body>` | all allowed users | GitHub issue shortcut |
| `/note <text>` | all allowed users | Notion page shortcut |
| `/contacts` | all allowed users | Show saved name→email memory |
| `/admin_users` | admin role | Manage pre-registered users from Discord |
| `/admin_settings` | admin role | View/edit guild settings from Discord |
| `/calendar_help` | all allowed users | Describe bot capabilities |

---

## Web Dashboard

### Routes

| Path | Access | Description |
|------|--------|-------------|
| `/` | public | Landing — redirect to `/login` if unauthenticated |
| `/login` | public | Show OAuth, local username/password, or both according to `/auth/methods` |
| `/auth/methods` | public | GET enabled login methods |
| `/auth/local` | local network | POST username/password when enabled |
| `/auth/google` | public | Start Google OAuth; callback is `/auth/google/callback` |
| `/auth/discord` | public | Start Discord OAuth; callback is `/auth/discord/callback` |
| `/dashboard` | authenticated | Home — connected integrations, recent activity |
| `/integrations` | authenticated | Connect / disconnect Google Calendar, GitHub, Notion |
| `/admin/users` | admin | Pre-register students, promote/demote admins, revoke access |
| `/admin/settings` | admin | Guild settings (timezone, model, allowed roles/channels) |
| `/admin/audit` | admin | Agent action log with filters |
| `/logout` | authenticated | Clear session |

### Admin: User Management

- Table of pre-registered users (name, Google email, Discord ID, role, added by, date)
- Add user form: Google email required, Discord ID optional, role selector
- Remove user (revokes dashboard access; does not ban from Discord bot)
- Promote/demote between student and admin

### Admin: Guild Settings

- Team timezone (IANA selector)
- Gemini model (dropdown of available models)
- Allowed Discord role IDs (comma-separated)
- Admin Discord role IDs
- Allowed channel IDs
- Mention channel IDs
- Save writes to `guild_settings` table; bot reads on each request (no restart needed)

### Admin: Audit Log

- Table: timestamp, user, Discord channel, tool called, input summary, status (success/error)
- Filter by user, tool, date range
- Paginated (50 per page)

### Integrations Page (per user)

- Card per integration (Google Calendar, GitHub, Notion)
- Connected state: shows connected account name + disconnect button
- Disconnected state: Connect button → Composio OAuth flow → callback → store `composio_id`

---

## Data Model

### Postgres Schema

```sql
-- Pre-registered users (admin-managed allowlist)
users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  uw_email      text UNIQUE,  -- legacy name: Google email from any domain
  local_username text UNIQUE, -- nullable, added in migration 003
  password_hash text,        -- nullable scrypt hash, never exposed in APIs
  discord_id    text UNIQUE,
  display_name  text NOT NULL,
  role          text NOT NULL DEFAULT 'student',  -- 'admin' | 'student'
  added_by      uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now()
)

-- Web sessions
sessions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token       text NOT NULL UNIQUE,
  provider    text NOT NULL,  -- 'google' | 'discord'
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
)

-- Composio connections per user
integrations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider      text NOT NULL,  -- 'googlecalendar' | 'github' | 'notion'
  composio_entity_id  text NOT NULL,
  connected_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, provider)
)

-- Agent conversation memory (per channel)
conversation_history (
  id          bigserial PRIMARY KEY,
  guild_id    text NOT NULL,
  channel_id  text NOT NULL,
  role        text NOT NULL,  -- 'user' | 'model' | 'tool'
  content     text,
  tool_name   text,
  tool_input  jsonb,
  tool_result jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
)

-- Per-user facts the agent learns
user_facts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key         text NOT NULL,
  value       text NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, key)
)

-- Agent action audit log
agent_actions (
  id          bigserial PRIMARY KEY,
  user_id     uuid REFERENCES users(id),
  discord_id  text,
  guild_id    text,
  channel_id  text,
  tool        text,
  input       jsonb,
  output      jsonb,
  status      text NOT NULL,  -- 'success' | 'error'
  duration_ms int,
  created_at  timestamptz NOT NULL DEFAULT now()
)

-- Guild configuration (replaces .env for runtime settings)
guild_settings (
  guild_id            text PRIMARY KEY,
  timezone            text NOT NULL DEFAULT 'America/Los_Angeles',
  model               text NOT NULL DEFAULT 'gemini-2.5-flash-lite',
  allowed_role_ids    text[] NOT NULL DEFAULT '{}',
  admin_role_ids      text[] NOT NULL DEFAULT '{}',
  allowed_channel_ids text[] NOT NULL DEFAULT '{}',
  mention_channel_ids text[] NOT NULL DEFAULT '{}',
  context_limit       int NOT NULL DEFAULT 12,
  updated_at          timestamptz NOT NULL DEFAULT now()
)

-- Name-to-email contact memory (replaces contacts.json)
contacts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  guild_id      text NOT NULL,
  name_key      text NOT NULL,  -- normalized lowercase
  display_name  text NOT NULL,
  email         text NOT NULL,
  added_by      text,  -- Discord user ID
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (guild_id, name_key)
)
```

---

## Infrastructure

### Docker Compose Stack

```
postgres:16-alpine    — shared database
migrate               — one-shot Alembic upgrade head
bot                   — Discord gateway + Gemini agent
dashboard             — FastAPI API server (port 8000)
frontend              — Next.js app (port 3000)
```

Reverse proxy (nginx or Caddy) sits in front, maps domain to frontend:3000 and `/api` to dashboard:8000. Not included in this repo — operator-configured.

### Environment Variables

**Shared (all services)**
```
DATABASE_URL=postgresql+asyncpg://dobby:password@postgres:5432/dobby
```

**Bot**
```
DISCORD_TOKEN=
DISCORD_GUILD_ID=
GEMINI_API_KEY=
COMPOSIO_API_KEY=
GEMINI_MODEL=gemini-2.5-flash-lite
TEAM_TIMEZONE=America/Los_Angeles
```

**Dashboard**
```
AUTH_MODE=oauth     # oauth, local, or both
SECRET_KEY=          # required for all login modes
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
COMPOSIO_API_KEY=
DASHBOARD_URL=       # e.g. https://dobby.uw.edu
BOOTSTRAP_ADMIN_EMAIL=  # optional OAuth seed; skipped in local mode or if an admin exists
API_URL=http://localhost:8000  # use server LAN address for another machine; rebuild frontend
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Bot | Python 3.12+, discord.py 2.x |
| Agent LLM | Google Gemini (google-genai) |
| Tool integration | Composio (`composio` SDK) |
| Dashboard API | FastAPI, authlib, SQLAlchemy 2 (async) |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Frontend | Next.js 15 (App Router), shadcn/ui, TanStack Query |
| Container | Docker + Docker Compose |

---

## Out of Scope (v2)

- Multi-guild support (single guild only)
- Message queue / background task workers
- Voice channel integration
- Recurring calendar events
- Email notifications
- Mobile app
