# Dobby research findings

> **Historical audit snapshot:** the findings below describe the tree at audit time, before the authentication changes. They are preserved as evidence, not current deployment instructions. Current login supports `AUTH_MODE=oauth`, `local`, or `both`; Google no longer requires a UW domain. Migration 003 adds local credentials; the local-admin CLI creates/resets admins and revokes their sessions. OAuth bootstrap, session creation and logout transaction handling have since changed; the original C2, the authentication portions of C3, and the bootstrap duplicate-user behavior in H9 should not be read as current findings. C1 has also been addressed: the migration environment now uses the installed asyncpg driver rather than stripping it from the URL. Driver-selection regression tests and offline SQL generation pass; live Postgres migration execution remains a separate deployment check. Other findings have not been re-audited here. See [README setup](../README.md#step-5-dashboard-login), [dashboard architecture](DASHBOARD_ARCHITECTURE.md), and [deployment](DEPLOYMENT.md#dashboard-authentication-and-lan-access).

Scope: working tree of branch `Testing-and-Reminder-Service-Functionality` on 2026-09-17. Read-only audit; nothing fixed here.
Confidence: **R** = confirmed at runtime, **S** = confirmed by static reading, **?** = suspected, needs runtime.

Sections 1–7 are the consolidated register (filled in Phase 6). Appendix A holds the raw Phase-1 static sweep so no observation is lost.

## 0. Read this first

Shipped `compose.yaml` cannot start: `migrate` fails (C1) → `bot`/`dashboard` never start. Even with migrations applied by hand, the dashboard dies at startup (C2). Even with the seed skipped, every write route 500s (C3) and the Docker frontend cannot reach the API (C4). Net: **the dashboard has never worked end-to-end in this tree**, and nothing in CI would have said so (H14). The bot's own path (Discord → Gemini → Composio) is independently plausible but freezes the process per request (H1) and lets a double-click publish twice (H2).

## 1. Critical (blocks deployment or breaks trust boundary)

| ID | Finding | Where | Evidence |
|---|---|---|---|
| C1 | `migrate` service has no sync Postgres driver: `env.py` strips `+asyncpg` → psycopg2 required, not installed anywhere. Whole stack gated on it. | `migrations/env.py:11`, `requirements.txt`, `compose.yaml:24-34,70-72,98-100` | B1, B2 (R) |
| C2 | Dashboard startup crashes: `seed.py` does `execute()` then `db.begin()` on one AsyncSession → `InvalidRequestError` in lifespan. `BOOTSTRAP_ADMIN_EMAIL` is mandatory in compose, so this path always runs on first boot. | `dashboard/seed.py:21,35`, `main.py:22`, `compose.yaml:97` | B4, B5 (R) |
| C3 | Same pattern in every write route (`Depends(get_db)` shared with `get_current_user`, which already ran SELECTs): `PATCH /me`, `POST/PATCH/DELETE /admin/users*`, `/role`, `/integrations/*/callback`, `DELETE /integrations/*`, `delete_session`, OAuth callbacks' `create_session` (after user SELECT). All → 500 `Internal error`. Admin UI is read-only in practice; login likely fails too (callback path untested, same shape). | `dashboard/main.py:70`, `routers/admin.py:79,106,133,163`, `routers/integrations.py:137,172`, `auth.py:88,98`, `routers/auth.py:88→101,151→164` | B6 (R); callbacks (S) |
| C4 | Frontend SSR guard fetches `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) from inside the frontend container → hits itself → every `/dashboard/*` → `/login`. Compose passes the same browser-facing URL for build and runtime; no internal URL exists. | `frontend/src/app/dashboard/layout.tsx:6-13`, `compose.yaml:110-119` | B8 (R) |
| C5 | Logout does not invalidate the session (C3 in `delete_session`); cookie remains valid for 7 days after "Sign out". A stolen/forgotten cookie survives logout. | `dashboard/auth.py:93-99`, `routers/auth.py:178-190` | B7 (R) |
| C6 | Fresh clone cannot build the frontend image: `package-lock.json` and `public/` untracked (not ignored, never added). Docs claim they are committed. | `frontend/Dockerfile:3-4,21`, `docs/FRONTEND_ARCHITECTURE.md:116-117` | B3 (R) |

## 2. High

| ID | Finding | Where | Evidence |
|---|---|---|---|
| H1 | `generate_content` is synchronous and awaited nowhere; called on the event loop with a 60 s timeout → entire bot (all users, Confirm buttons, view timeouts, heartbeat) stalls per request. | `bot/agent.py:43,82` | B11 (R) |
| H2 | Confirm double-press → `execute()` twice → duplicate LinkedIn/Instagram publish. `_disable()` only takes effect after Discord acks the edit. | `bot/integrations/discord/confirm.py:45-78` | B12 (R) |
| H3 | Calendar create/update/**delete** and Notion writes are Gemini-callable with NO confirm gate, while channel history (from any member, allowlisted or not), Notion page bodies and calendar descriptions are fed to the model. One "treat as untrusted" line is the only defence. | `bot/integrations/google_calendar/__init__.py:11-17`, `agent.py:26,63-68,118-149`, `context.py:20-39` | S |
| H4 | Admin can set any user's `discord_id` (Discord-login identity) with no uniqueness/format check → impersonation primitive; also `uw_email` unvalidated on create; `role` free-text on create. | `routers/admin.py:115-143`, `schemas.py:41,45,54`, `routers/auth.py:151` | S |
| H5 | Dashboard "Connected" state is fiction: callback records a row with no proof from Composio (GET, CSRF-able via Lax), disconnect deletes the row only (Composio token persists, bot keeps working), bot never reads the table. | `routers/integrations.py:117-178`, `bot/composio.py:52` | S; B6 shows callback 500s today |
| H6 | Two disjoint allowlists: env `ALLOWED_USER_IDS/ROLE_IDS` gates the bot; `users` table gates the dashboard. Removing a person in the dashboard does not stop bot use; adding does not grant it. `/email` needs both. | `bot/config.py:84-89`, `dashboard/routers/admin.py` | S |
| H7 | Tool executes BEFORE its audit insert; if `record_action` raises (schema drift, DB down) the exception escapes → user told "failure" though the calendar event / Notion page was created → retry duplicates. Confirm path swallows audit errors instead (asymmetric). | `bot/agent.py:118-129`, `confirm.py:62-75` | S |
| H8 | Deleting any user who ever triggered a tool call, or who added another user, fails with 500 (FKs without `ondelete`; ORM lacks the `agent_actions.user_id` FK so SQLAlchemy can't null it). | `migrations/versions/001_initial.py:27,78`, `dashboard/models.py:86-88` | S |
| H9 | Demote-last-admin / self-demote has no guard; after restart `seed.py` re-inserts the bootstrap email → unique violation → crash-loop (once C2 is fixed). | `routers/admin.py:146-172`, `seed.py:12-38` | S |
| H10 | Secure/https-only cookies keyed on `ENV=production`, which nothing sets. Session token stored plaintext in DB; random suffix is outside the signature; expired rows never purged. | `dashboard/auth.py:17-61`, `main.py:30-34` | S |
| H11 | Dashboard container runs as root with every secret (SECRET_KEY, both OAuth secrets, Composio key, DB password) in env, no hardening, published on 0.0.0.0:8000. Frontend 0.0.0.0:3000. Bot is hardened; dashboard is not. | `dashboard/Dockerfile`, `compose.yaml:79-126` | B9 (R) |
| H12 | Migration 002 is lossy/duplicating: multi-guild same-name contacts → one email wins nondeterministically; whitespace/Unicode-variant names → duplicate directory users; first-name-only contacts become users that shadow real members in `find_user_by_name`; downgrade restores nothing. | `migrations/versions/002_*.py:23-40` | B10 (R) |
| H13 | `find_user_by_name` fuzzy ≥0.6 returns full email to Gemini → calendar invite (event details) to the wrong person: jon→john, sam→sami, tom→tim, ben→bea, lee→leo… Short display names are the common case. Full emails of @mentioned users also enter the system prompt and can be echoed to the channel; `mask()` only on `/email show`. | `bot/memory.py:26-57`, `agent.py:73-76`, `google_calendar/tools.py:9-19` | S (ratios computed) |
| H14 | CI never installs, lints, tests, type-checks or builds `dashboard/` or `frontend/`; no dashboard tests exist. C1–C6 are invisible to CI. `publish.yml` ships only the bot image; `update-pi.sh` refreshes only `bot` while `migrate` stays a stale local build → schema drift by design. | `.github/workflows/ci.yml`, `publish.yml`, `scripts/update-pi.sh` | S |
| H15 | `deploy/dobby-update.service` runs a checkout script as root every 5 min, auto-pulling `:main` with no digest pin: repo/GHCR compromise = root on the Pi within 5 min. | `deploy/dobby-update.service`, `deploy/dobby-update.timer`, `compose.registry.yaml` | S |

## 3. Medium

| ID | Finding | Where |
|---|---|---|
| M1 | Prompt-injection via `display_name` (unbounded, no newline strip, admin-set) into the SYSTEM prompt, the request text and the calendar tool result. `/notion note` title/body and `/schedule` text are interpolated into instructions. | `agent.py:73-76`, `context.py:58-60`, `notion/commands.py:31,50` |
| M2 | Raw Composio/provider exception strings reach Gemini (all integrations) and the channel (Instagram) → provider error bodies/URLs/entity id leak. | `bot/composio.py:82`, `instagram/publish.py:38`, `instagram/commands.py:18` |
| M3 | `candidate.content.parts` unguarded → `content=None` (MAX_TOKENS/safety) → AttributeError escapes `Agent.run`; only `errors.APIError` caught, transport errors propagate; on Gemini error `ctx.pending` is still returned so stale drafts can be confirmed. | `agent.py:92-105` |
| M4 | Preview truncated to 2000 chars but full text published (Instagram caption up to 2200) → confirm without seeing it all. Confirm doesn't re-check `config.allows`. | `client.py:169`, `confirm.py:35-39` |
| M5 | Unauthenticated Discord spam on a mention channel costs 2–3 REST calls each (`fetch_member`, `fetch_channel`) before the cooldown check → rate-limit DoS of the bot token. Replies to Dobby's messages count as mentions. | `client.py:65-115` |
| M6 | 422 bodies render as `[object Object]`; unhandled 500s lack CORS headers → "Failed to fetch"; remove/role/disconnect mutations have no error UI; delete has no confirmation; student on admin pages sees "No users" + a live Add button. | `frontend/src/lib/api.ts:13`, `main.py:36`, `users/page.tsx`, `audit/page.tsx` |
| M7 | Audit status vocabulary mismatch (bot `ok` vs FE `success`) → "Success"/"Pending" filters always empty; `tool` filter exact vs substring; `total` discarded → 201st user invisible, 25-row audit shows empty page 2. | `agent.py:127`, `audit/page.tsx:32-37,98-100`, `api.ts:40,60` |
| M8 | `discord_id` stored verbatim (no trim) by admin → ` 123`/`<@123>` silently breaks `/email`, mention resolution, Discord login and audit attribution. | `schemas.py:42,54`, `users/page.tsx:64,176` |
| M9 | `request.url_for` builds OAuth/Composio redirect URIs from the Host header; uvicorn runs without `--proxy-headers`/`--root-path` → wrong scheme/host behind any proxy; Composio `redirectUrl` used unchecked. | `dashboard/Dockerfile:8`, `routers/auth.py:61,117`, `routers/integrations.py:96-100` |
| M10 | CORS exact-string origin (`DASHBOARD_URL` trailing slash breaks everything); every request preflighted (`Content-Type` on GET/DELETE); separate registrable domains impossible with Lax. | `main.py:38`, `api.ts:8` |
| M11 | No rate limiting on the dashboard; 403 messages distinguish "not UW" from "not pre-registered" (enumeration). Session middleware reuses `SECRET_KEY`. | `routers/auth.py:81-98,157-161` |
| M12 | `COMPOSIO_ENTITY_ID` handled differently (bot strips + dotenv; dashboard raw, no dotenv) → silent divergence outside compose; stored entity id never compared to live env; `connected_at` never refreshed on reconnect. | `bot/config.py:36,74`, `routers/integrations.py:27,141-146` |
| M13 | `.gitignore` lacks `*.sql`; README/DEPLOYMENT tell users to `pg_dump > backup.sql` into the repo root (emails + plaintext session tokens); `check_secrets.py` won't flag it. No `frontend/.dockerignore`/`dashboard/.dockerignore`. | `.gitignore`, `README.md:248`, `docs/DEPLOYMENT.md:47` |
| M14 | ConfirmViews are process-local and non-persistent; the 5-minute Pi update timer silently kills every pending preview. Cooldowns likewise. | `confirm.py`, `deploy/dobby-update.timer` |
| M15 | Worst-case context: 500 msgs × ~540 chars ≈ 67k tokens per request into `gemini-2.5-flash-lite`, configurable via env with no cost/latency warning. | `bot/config.py:61`, `context.py:17,29` |

## 4. Low

| ID | Finding | Where |
|---|---|---|
| L1 | `TEAM_TIMEZONE` code default `America/Denver` vs `America/Los_Angeles` everywhere else. | `bot/config.py:52` |
| L2 | `/email` never defers (3 s Discord window); `/email` has no cooldown. | `discord/commands.py:32-67` |
| L3 | `confirm.py:77` final edit outside try; `on_timeout` no-op if `view.message` unset. | `confirm.py:77,86-92` |
| L4 | `run_action` `to_thread` has no timeout; `find_key` first-match recursion can pick a same-named key from an unrelated level; `gemini_schema` drops `$ref/$defs`. | `bot/composio.py:16,85-109` |
| L5 | `UserUpdate.display_name: null` → NOT NULL violation → 500; whitespace-only names stored; `POST /admin/users` response `created_at` may be null; dup `discord_id` → 500 not 409. | `routers/admin.py`, `models.py:29` vs `001:25` |
| L6 | Model/migration drift (nullability, FK, constraint names); `env.py` `target_metadata=None` so autogenerate can never catch it. | `dashboard/models.py`, `migrations/env.py:16,24` |
| L7 | `bot/main.py` swallows tracebacks (`type(exc).__name__` only); `Config` requires `DATABASE_URL` but doesn't store it; `db.py` re-reads env. | `bot/main.py:18-41`, `config.py:37`, `db.py:11` |
| L8 | Stale cookie never cleared for 7 days; `/login` has no signed-in redirect; OAuth 403s strand the user on raw JSON at the API origin. | `frontend/src/app/page.tsx`, `login/page.tsx`, `routers/auth.py:82-98` |
| L9 | `ruff` on `dashboard/` + `migrations/` (not in CI): 3× F401, 6× E402. | B14 |
| L10 | Unpinned `>=` deps in `dashboard/requirements.txt` and `composio-core>=0.7.0`; GH actions on floating major tags. | `dashboard/requirements.txt`, `.github/workflows/*.yml` |

## 5. Dead / disconnected code

- Tracked in git, deleted in worktree (delete for real or restore): `bot/contacts.py`, `bot/format.py`, `bot/tools.py`, `compose.auth.yaml`, `scripts/link_google.py`, `tests/test_bot.py`, `tests/test_contacts.py`, 29× `bot/responses/*.txt`, `.claude/PLAN-dobby-restructure.md`, `.claude/PROGRESS-dobby-restructure.md`, `frontend/src/app/dashboard/contacts/page.tsx`, `frontend/src/app/dashboard/admin/settings/page.tsx`. No code references survive.
- Untracked but required: `frontend/package-lock.json`, `frontend/public/robots.txt`, `migrations/versions/002_*.py`, `bot/composio.py`, `bot/integrations/**`, `tests/integrations/**`, `tests/test_composio.py`, `tests/test_models.py`, 9 new `bot/responses/*.txt`, `docs/{BOT,DASHBOARD,FRONTEND}_ARCHITECTURE.md`. The working tree is the only copy of the new architecture.
- Unreferenced symbols: `bot/db.py:25 get_session`; `dashboard/schemas.py:76 SessionUser`; `Integration.app` (`base.py:69`, set 4×, read 0×; mapping duplicated in `routers/integrations.py:33-49`); `Bot.member_allowed(mention=)` param never varied; `dashboard/auth.py:117-121` computes `user_id` then ignores it; `auth.py:7 Cookie`, `models.py:13 DeclarativeBase`, `routers/auth.py:17 get_current_user` imports unused.
- Write-only DB columns (nobody reads): `users.added_by`, `sessions.provider`, `sessions.created_at`, `integrations.composio_entity_id`, `integrations.connected_by`, `agent_actions.user_id/guild_id/channel_id`. The entire `integrations` table has no reader except the dashboard list page.
- Frontend deps never imported: `@radix-ui/react-dropdown-menu`, `@radix-ui/react-toast`.
- Unreachable branches: "Nothing to update" 422s (`main.py:68`, `admin.py:123-127`) — FE always sends fields. `SUPPORTED_PROVIDERS` vs `mapping` parallel lists.
- Untested: `bot/main.py`, `bot/db.py`, all `*/commands.py`, notion integration, every line of `dashboard/`, every line of `frontend/`.
- Stale pycs for removed modules in `bot/__pycache__`, `tests/__pycache__`.

## 6. Docs drift

- `docs/PRD.md` describes a different product (GitHub tool, `/ask`, `/issue`, `/contacts`, `/admin_settings`, per-user integrations, `conversation_history`, `user_facts`, `guild_settings`, conversation memory). Lines 5, 11, 70, 112, 114, 132, 204, 217, 241-255.
- `docs/FRONTEND_ARCHITECTURE.md:5,34,95` settings page/query key; `:116-117` claims lockfile + `public/` committed (false → C6).
- `docs/DASHBOARD_ARCHITECTURE.md:4-5,18` guild settings in admin router (removed); duplicated table rows `:52/53, 63/64, 103/104`; `:98,112` documents `ENV` and admits compose never sets it.
- `ARCHITECTURE.md:67` "revoking in Composio cuts it off" — true, but the dashboard Disconnect button does not revoke (H5). `:83` claims CI verification without noting dashboard/frontend are excluded (H14). `:29` "bot ... read-only filesystem" true; implies dashboard is similar — it is not (H11).
- `bot/config.py:35` "Compose mounts this file read-only" — no mount exists. `bot/memory.py:3` points at `bot/context.py` (moved).
- `README.md:248` / `docs/DEPLOYMENT.md:47` `pg_dump > backup.sql` in repo root (M13). `README.md:316` says install dashboard reqs before pytest; nothing imports dashboard. `DOBBY_IMAGE` required by `compose.registry.yaml` but missing from `.env.example`. `ci.yml:36` builds `calendar-bot:test` (old name).
- `/help` says "nothing is stored" while `agent_actions` stores discord_id, guild_id, channel_id, tool, status per call (`discord/commands.py:84` vs `memory.py:82-84`; `docs/BOT_ARCHITECTURE.md:104` is accurate).
- `docs/DASHBOARD_ARCHITECTURE.md:78` "users can exist without login identifiers" — true post-002 but those users can never log in and are invisible to the bot except as invite targets.

## 7. Checked, not a bug

- All `/admin/*` and `/integrations/*` routes carry `require_admin`; `PATCH /me` accepts only `calendar_email` (`MeUpdate`); pydantic drops unknown keys so the `setattr` loops cannot mass-assign.
- No raw/f-string SQL anywhere; bot uses bound params. No `subprocess`/shell in bot. No `dangerouslySetInnerHTML`; reflected `detail` strings are static or rendered as text by React.
- Session tokens are never in JS (httpOnly cookie); forging needs a DB row (signature alone is insufficient).
- `calendar_email` validation is identical in bot (`valid_email`) and dashboard (`clean_email`); null-clearing via `exclude_unset` works.
- Bot container hardening (non-root, read-only, cap_drop, no ports, tmpfs) is real. `check_secrets.py` and `.dockerignore` keep `.env` out of images.
- All 18 response pools referenced, 20 lines each, `FALLBACK` matches. No test imports a removed module. Frontend `tsc` and `next lint` clean.
- Nullable-in-BE/required-in-FE type drift is not a live bug: DB NOT NULL constraints prevent nulls on reads.
- `explicit null` to clear emails works; `role` union is enforced by `/role` route (only `POST /admin/users` is unvalidated).

## Runtime log index (Phase 3)

Scratchpad `logs/`: `phase0-git.txt`, `phase0-pytest.txt`, `phase0-ruff-dashboard.txt`, `phase0-frontend.txt`, `phase3-migrate.txt` (B1), `phase3-freshclone-frontend.txt` (B3), `phase3-alembic-container.txt`, `phase3-dashboard.txt` (B5, B7), `phase3-dashboard-routes.txt` (B6), `phase3-container-reach.txt` (B8, B9), `phase3-migration002.txt` (B10), `phase3-gemini-block.txt` (B11), `phase3-confirm-reentry.txt` (B12). Repro scripts: `autobegin_repro.py`, `dash_runtime.sh`, `dash_routes2.sh`, `gemini_block.py`, `confirm_reentry.py`.

## 8. Vulnerability threat table (Phase 4)

Abbrev: `r/auth.py` = `dashboard/routers/auth.py`; `client.py`/`confirm.py`/`context.py` = `bot/integrations/discord/*`.

| ID | actor → target | precondition | attack path | impact | sev | conf |
|---|---|---|---|---|---|---|
| V1 | Non-allowlisted guild member (or anyone controlling Notion/calendar text, or a nickname) → team Calendar/Notion | Can post in a mention channel; an allowlisted member later mentions Dobby there | Plant ≤500-char text or nickname (`context.py:17,44-45`); `gather_context` reads last N human messages, no role filter (`context.py:29-34`) → `role=user` content with only a prose "untrusted" note (`agent.py:26,65-68`) → Gemini may call `GOOGLECALENDAR_DELETE/UPDATE/CREATE_EVENT`, `NOTION_CREATE_NOTION_PAGE/ADD_PAGE_CONTENT` unvalidated, no Confirm (`agent.py:118-120,146-149`); tool results re-enter the loop (`:130-131`) | Unconfirmed deletion/edit of team events, Notion writes/exfil, PII via `lookup_calendar_email`; audit blames the innocent requester | High | Med (LLM-dependent; chain complete) |
| V2 | Whoever influences Gemini output → requester's Confirm | Draft queued | `send_result` = text + preview then `[:2000]` (`client.py:161,169`); text up to 4096 tokens → preview cut or gone, buttons still attached; IG caption 2200 > 2000 | Requester confirms a PUBLIC post they never fully saw | Med | High |
| V3 | Allowlisted member (or V1 injector) → members' PII | Mention or name a registered user | Full `calendar_email` into SYSTEM prompt (`context.py:56,62`, `agent.py:73-76`); `lookup_calendar_email` returns full address incl. fuzzy ≥0.6 (`memory.py:44-57`); reply is public | Any channel reader learns members' emails; `mask()` only on `/email show` | Med | High |
| V4 | Holder of a stolen `dobby_session` cookie → dashboard | Cookie obtained (V5/V9/LAN) | Logout never deletes the row (B7); only revocation is user deletion, which 500s for anyone with audit rows (H8); no session list/kill | 7-day unrevocable session; admin cookie theft = persistent admin | Med | High (R) |
| V5 | LAN / non-HTTPS hop → any dashboard user | Deployed as documented (plain http, `ENV` unset) | `secure=False` (`auth.py:48-58`), `https_only=False` (`main.py:33`), ports on 0.0.0.0, no TLS | Cleartext session + OAuth-state cookies; with V4 = durable takeover | Med (Low if isolated LAN) | High |
| V6 | Anyone who can push `main` / write GHCR → Pi bot + DB | Timer installed per DEPLOYMENT.md | `:main` tag, `pull_policy: always`, no digest; `update-pi.sh` every 5 min as root | Attacker code as bot uid within 5 min holding Discord/Gemini/Composio keys + DB URL; DB user owns all tables → insert `sessions` row = dashboard admin | Med (high impact, needs repo/registry compromise) | High |
| V7 | Rogue/compromised admin → other admins, members, org accounts | Admin session | No admin mutation audited; `PATCH /admin/users/{id}` sets any `discord_id` → Discord login keyed solely on it (`r/auth.py:151`) → session as any user incl. bootstrap admin; free-text `role` on create; no self-demote/last-admin guard; can point any member's `calendar_email` at an outsider (invites carry event details) | Untraceable privilege changes, admin lockout, forged attribution, invite leakage | Med (insider) | High |
| V8 | Any Discord account whose snowflake matches a `users.discord_id` row → dashboard | Admin typo / stale or reassigned ID | Discord callback uses only `id` (`r/auth.py:136,151`); no guild-membership or UW check | Outsider dashboard access (admin-level if row is admin) | Low | High |
| V9 | Maintainer (accidental) → everyone with a session | Follows README:248 `pg_dump > backup.sql` in repo root | No `*.sql` ignore; `check_secrets.py` matches neither `.sql` nor the token shape; tokens live 7 days and survive logout (V4) | Public repo = live admin sessions + member PII | Med | High |
| V10 | Attacker site visited by logged-in admin → `integrations` table / admin's browser | Cookie Lax | Top-level GET carries cookie: `/integrations/{p}/callback` (no state) writes "connected" (post-C3 fix); `/connect` initiates Composio OAuth under the service entity | Fake "Connected" rows; nuisance OAuth. POST/PATCH/DELETE not reachable (N5) | Low | High |
| V11 | Local user owning `/opt/dobby` → root | Timer installed; `chown $(id -u) /opt/dobby` per docs | root runs `/opt/dobby/scripts/update-pi.sh` and reads compose/.env from user-writable tree | Root every 5 min for that user (already docker-group = root-equivalent) | Low | High |
| V12 | Any guild member → bot availability | Can post in a mention channel | `member_allowed` (2–3 REST calls) + public "not authorized" reply run before `take_cooldown` (`client.py:111-114`); allowlisted requests freeze the loop ≤60 s (B11) | Rate-limit exhaustion, channel spam, stall for all users | Low | High |
| V13 | Any future dashboard bug / docker-group user → all secrets | — | Dashboard root, unhardened, all secrets in env (B9); `docker inspect` shows them | Blast radius of any dashboard flaw = every credential + DB | Low | High |
| V14 | Composio/provider → channel readers & model | Instagram command fails | `execute_tool` returns raw response body as `str(exc)`; Instagram posts it to channel; all tool errors go to Gemini | Leaks entity id / connected-account ids / provider JSON (not the API key) | Low | High |
| V15 | Requester → own draft | Double Confirm within execute window | No `is_finished` guard (`confirm.py:35-53`) → `execute()` 2× (B12) | Duplicate public posts | Low | High (R) |
| V16 | Unauthenticated internet → API | Port 8000 reachable | `/docs`, `/redoc`, `/openapi.json` enabled (`main.py:27`) | Full route/schema disclosure | Info | High |
| V17 | Supply chain | — | All `>=` deps, no hashes; floating action tags; no committed frontend lockfile | Unreviewed upstream code into images | Info | High |
| V18 | Operator misconfig | — | `SECRET_KEY` any non-empty string; `changeme` DB password in example; Instagram `image_url` only `startswith("http")` | Weak defaults, not directly exploitable (N3) | Info | High |

### Checked, NOT vulnerable (security-specific)
- N1 Every `/admin/*`, `/integrations/*` handler has `require_admin`; role re-checked per request.
- N2 Mass assignment: pydantic v2 default `extra=ignore` → `setattr` loops see declared fields only.
- N3 Cookie forging/fixation: DB row on the full token is authoritative; cookie only set server-side post-OAuth.
- N4 OAuth `state`+`nonce` kept in authlib's signed session cookie and verified on callback.
- N5 CSRF on POST/PATCH/DELETE: Lax cookie + JSON-only parsing + single exact CORS origin → not reachable cross-site.
- N6 Host-header → `url_for`: affects only the attacker's own request; providers reject unregistered URIs.
- N7 No open redirect (targets are env/Composio-controlled). N8 403 texts reveal only the caller's own status. N9 `hd=uw.edu` checked on the signature-validated id_token.
- N10 Every slash command gated (`allowed`/`gate`), all `guild_only`, synced to one guild; DMs excluded by intents; private threads verified; bots/webhooks skipped.
- N11 Stranger cannot Confirm (B12). N12 No SQLi (bound params/ORM), no XSS sink. N13 Bot logs exception type names only; Composio key travels in a header, never in error bodies. N14 No `pull_request_target`; `GITHUB_TOKEN` minimally scoped. N15 No IDOR (`/me` self-only; integrations are service-wide).

---

## Appendix A — Phase 1 static sweep (raw, unverified)

### A.1 Bot package

Entry & flow
- `bot/main.py:12-45` `main()` → `Config.load()`; `--check` returns; else lazy-import `Bot`, `bot.run(token, log_handler=None)`. Both `except` swallow traceback, log only `type(exc).__name__`, `raise SystemExit from None`.
- Registry `bot/integrations/__init__.py:16-46`: 4 integrations; `build_registry` merges Composio declarations + local tools into one `types.Tool`; `owner` map; prompts concatenated. Discord = transport.
- `Bot.__init__` `client.py:20-35`: intents guilds/guild_messages/message_content; toolset from `composio_key`; `Agent(...)`; `agent.toolset` patched from outside; `register_commands()`.
- `on_message` `client.py:101-135`: guild/mention-channel filter → `self.user in message.mentions` → `member_allowed` → `take_cooldown` → strip mention → reject empty/>2000 → placeholder → `ask_agent` → `send_result`.
- `ask_agent` `client.py:137-150`: `gather_context(limit=context_limit)` then one `SessionLocal()`; `resolve_mentions`; `agent.run`.
- Agent loop `agent.py:80-136`: `MAX_TOOL_CALLS=8`; local tools vs Composio via `registry.owner`; `record_action` per call; `session.commit()` per round.
- Confirm: `RunContext.queue()` `base.py:38-47` → `AgentResult.pending` → `preview_text` + `ConfirmView(pending, requester_id, guild_id, channel_id)` `client.py:152-176`, `confirm.py:21-93`; 120 s timeout; `interaction_check` requester-only; audit `"{integration}.publish"` on Confirm.

Bot ↔ DB / dashboard
- Bot DB = raw SQL only, `bot/memory.py`: users by discord_id `:17-23`, by name `:26-57`, `UPDATE users SET calendar_email` `:60-66`, `INSERT agent_actions` with `user_id` subselect `:69-94`.
- Bot never touches `sessions` or `integrations` → does not check dashboard "connected" state before calling Composio.
- No HTTP between bot and dashboard (grep httpx/requests/aiohttp/8000 in bot/ = 0).
- Shared env: `DATABASE_URL`, `COMPOSIO_API_KEY`, `COMPOSIO_ENTITY_ID` (dashboard mirrors at `routers/integrations.py:27`).
- `.env.example:50 API_URL` only consumed by frontend service.
- `bot/config.py:37` requires `DATABASE_URL` but never stores it; `bot/db.py:11` re-reads env.
- `bot/config.py:35-36` comment "Compose mounts this file read-only" — no such mount; `.dockerignore` excludes `.env*`.
- Drift: `001_initial.py:24` users.display_name NOT NULL vs `dashboard/models.py:29` nullable; `agent_actions.user_id` FK in 001:78, none in model.

Composio
- `composio.py:19-20` single toolset from `COMPOSIO_API_KEY`; single entity `Config.composio_entity` default `"dobby"`; passed at `agent.py:149`, instagram/linkedin publish + commands. No Discord user → entity mapping (intentional).
- `declarations_for` `:47-74` `check_connected_accounts=False`; on exception logs and returns `[]` → integration silently has no tools; unknown action names = warning only.
- `execute_tool` `:77-82` blanket except → `{"success": False, "error": str(exc)}` — raw exception string to Gemini and (Instagram) to channel via `instagram/publish.py:38` → `instagram/commands.py:18`.
- `run_action` `:85-87` `to_thread` no timeout.
- `find_key` `:90-109` unbounded recursive first-match (used for `creation_id` `instagram/publish.py:74`, LinkedIn URN `linkedin/publish.py:16-20`).
- `gemini_schema` drops `$defs/$ref` silently (`SCHEMA_KEYS` `:16`).

Dead / disconnected
- `bot/db.py:25-28 get_session()` no references.
- `Integration.app` `base.py:69` set by all 4, never read; mapping duplicated `dashboard/routers/integrations.py:33-49`.
- `member_allowed(..., mention=True)` `client.py:65` never called with False.
- All 18 `bot/responses/*.txt` referenced; `FALLBACK` keys match (asserted `tests/test_voice.py:12`).
- Tracked-but-deleted: `bot/contacts.py`, `bot/format.py`, `bot/tools.py`, `compose.auth.yaml`, `scripts/link_google.py`, `tests/test_bot.py`, `tests/test_contacts.py`, 29 `bot/responses/*.txt`, `.claude/PLAN-*.md`, `.claude/PROGRESS-*.md`, `frontend/.../contacts/page.tsx`, `frontend/.../admin/settings/page.tsx`. No import/compose reference survives. Textual: `bot/memory.py:3` docstring → `bot/context.py` (moved); `docs/PRD.md:112,70,204,217,254-255`.
- Stale pycs: `bot/__pycache__/context*.pyc`, `tools*.pyc`, `tests/__pycache__/test_bot*`, `test_context*`, `test_contacts*`.
- No tests for notion, `bot/main.py`, `bot/db.py`, any `commands.py`, dashboard.
- CI lints `bot scripts tests` only.

Unfinished / thin
- No TODO/FIXME/stubs in bot/.
- `agent.py:102` `candidate.content.parts` unguarded (content=None on MAX_TOKENS/safety) → AttributeError escapes. `:105` assumes iterable.
- `agent.py:82` sync `generate_content` on event loop, up to 60 s (`:43`).
- `agent.py:92-96` catches only `errors.APIError`.
- `Agent.toolset=None` until patched `client.py:33`.
- linkedin/google_calendar/notion commands catch bare Exception → generic_failure; only Instagram maps `UserError` (`instagram/commands.py:16-21`).
- `/email` `commands.py:32-67` never defers → 3 s window.
- `confirm.py:77` `edit_original_response` outside try.
- `confirm.py:86-92` `on_timeout` edits only if `self.message` set.
- No `create_task` anywhere; cooldowns pruned opportunistically `client.py:57-63`; `ConfirmView`s process-local, non-persistent.
- `config.py:58-62` non-numeric `CONTEXT_MESSAGE_LIMIT` → sentinel -1 → misleading message.

Security
- Authz `config.py:84-89` guild AND channel-allowlist AND (user OR role). Fail-closed startup `:50-51`. No admin bypass.
- Slash: `allowed`/`gate` + 10 s cooldown. `/email` uses `allowed` only, no cooldown, no defer.
- Mention path verifies view_channel, private-thread membership, allowlist `client.py:65-97`.
- Confirm: `interaction_check` requester-only; no `config.allows` re-check at press time; non-persistent views.
- `client.py:169` preview truncated 2000 vs full publish (IG caption 2200).
- No tokens in logs; `agent.py:148` logs entity id only. `composio.py:82` `str(exc)` reaches channel for Instagram.
- LLM inputs: raw request + channel history (marked untrusted); `/notion search|note` interpolate user text into instruction (`notion/commands.py:31,50`); `/schedule` raw request; DB `display_name`/`calendar_email` into SYSTEM prompt `agent.py:74-76`.
- SQL bound params only. No subprocess in bot/.
- Instagram URL check = `startswith("http")` (`instagram/publish.py:141,157`, `commands.py:43,64`).
- Gemini tool args forwarded unvalidated `agent.py:118,149` incl. `GOOGLECALENDAR_DELETE_EVENT` — no confirm gate on calendar/Notion writes.
- LinkedIn posts `visibility: PUBLIC` `linkedin/publish.py:27-38`.
- `mask()` on `/email show` (ephemeral); `find_user_by_name` returns full address to Gemini.

Tests
- Present: conftest, test_agent, test_composio, test_memory, test_models, test_voice, integrations/test_registry, discord/{client,commands,confirm,context}, google_calendar/test_calendar_tools, instagram/test_instagram_publish, linkedin/test_linkedin_publish.
- No test imports a removed module. `test_discord_commands.py:8` sibling import via rootdir sys.path (no `__init__.py`).
- `test_voice.py:17-27` asserts 20 lines per pool and FALLBACK==files.

### A.2 Dashboard backend

Routes
| Method / path | File:line | Auth | Tables |
|---|---|---|---|
| GET /health | main.py:49 | none | – |
| GET /me | main.py:54 | user | sessions, users |
| PATCH /me | main.py:59 | user | users (self) |
| GET /auth/google | routers/auth.py:59 | none | – |
| GET /auth/google/callback | auth.py:69 | none | users R, sessions I |
| GET /auth/discord | auth.py:115 | none | – |
| GET /auth/discord/callback | auth.py:121 | none | users R, sessions I |
| POST /auth/logout | auth.py:178 | none (cookie) | sessions D |
| GET /admin/users | admin.py:30 | admin | users |
| POST /admin/users | admin.py:54 | admin | users |
| DELETE /admin/users/{id} | admin.py:90 | admin | users (+sessions cascade) |
| PATCH /admin/users/{id} | admin.py:115 | admin | users |
| PATCH /admin/users/{id}/role | admin.py:146 | admin | users |
| GET /admin/audit | admin.py:179 | admin | agent_actions |
| GET /integrations | integrations.py:56 | admin | integrations |
| GET /integrations/{p}/connect | integrations.py:74 | admin | – |
| GET /integrations/{p}/callback | integrations.py:117 | admin | integrations upsert |
| DELETE /integrations/{p} | integrations.py:155 | admin | integrations |

- All `/admin/*` and `/integrations/*` have `require_admin`. `POST /auth/logout` unauthenticated. `GET .../connect` and `GET .../callback` are state-changing GETs (CSRF via Lax top-level nav). Callback writes "connected" with no Composio verification, no state nonce. No CSRF tokens anywhere.

Auth
- Token = `URLSafeSerializer(SECRET_KEY, salt="session").dumps(user_id) + "." + token_hex(16)` (`auth.py:26-28`); random suffix outside signature; `verify_token` result never compared to `session.user_id` (`:117` vs `:135`); DB row is authority.
- `SESSION_DAYS=7`; expiry via query; no cleanup of expired rows.
- Cookie httponly, lax, path=/, `secure=ENV=="production"` (`auth.py:48-61`); `ENV` set nowhere (compose.yaml:87-97, .env.example, Dockerfile). `SessionMiddleware(https_only=...)` same (`main.py:33`).
- CORS `main.py:36-42` single exact origin, credentials, `*` methods/headers.
- No passwords; OAuth-only. Google `hd=uw.edu` enforced twice. Discord has no domain equivalent.
- No rate limiting anywhere.
- Role free-text; check `!= "admin"` (`auth.py:149`). `UserCreate.role` unvalidated (`schemas.py:45`); only `/role` validates (`admin.py:153`).
- `seed.py:12-38` creates admin only if no admin exists; reseed hazard: demoted bootstrap user → duplicate `uw_email` → IntegrityError in lifespan → no start.
- OAuth client ids/secrets via `os.environ.get` → boot with None, fail at OAuth time. `COMPOSIO_API_KEY` unset → 503.

Integrations
- Service-owned, one row per provider, no user_id → no IDOR surface.
- Connect: `initiate_connection(redirect_url=request.url_for(...))` → Host-header derived; behind proxy w/o forwarded headers → internal URL handed to Composio. `connection_req.redirectUrl` unchecked (None → raises after except blocks).
- Callback upserts `{provider, composio_entity_id, connected_by}` → 302 `DASHBOARD_URL/dashboard/integrations`.
- No provider tokens stored. `IntegrationOut` exposes provider/connected_by/connected_at only.
- Disconnect deletes DB row only; Composio connection persists; doesn't validate provider (404).

Admin
- List returns full `UserOut` (uw_email, discord_id, calendar_email, role, created_at).
- Create: uniqueness on `uw_email` only → dup `discord_id` → IntegrityError → 500 (`admin.py:83-85`). `uw_email` unvalidated. Returned `created_at` None (server_default, no refresh).
- Delete: blocks self only; no last-admin guard; `users.added_by` FK no ondelete (001:27) → FK error → 500.
- Update: admin can set any user's `discord_id` → Discord-login identity (`routers/auth.py:151`) → impersonation primitive; no uniqueness pre-check → 500.
- Role: no self-demote / last-admin guard.
- Audit: `agent_actions` = bot telemetry only; NO admin mutation is logged. `AuditLogEntry` exposes discord_id, not user_id/guild/channel.
- Settings/contacts endpoints deleted (HEAD had `admin.py:149,171,263`).

Models vs migrations
- `users.display_name` NOT NULL (001:25) vs nullable model (models.py:29).
- `*.created_at`, `integrations.connected_at` NOT NULL vs Optional.
- `sessions.provider` NOT NULL (001:36) vs nullable (models.py:55).
- `agent_actions.status` NOT NULL (001:85) vs nullable (models.py:93).
- `agent_actions.user_id` FK in 001:78, none in model.
- `integrations.composio_entity_id` NOT NULL (001:46) vs nullable (models.py:73).
- `integrations.provider` unique: named constraint (002:55) vs inline `unique=True`.
- `models.py:13` unused `DeclarativeBase` import. `bot/models.py` no ORM overlap; `EMAIL` regex duplicated (`schemas.py:9`).
- `migrations/env.py:16,24` `target_metadata=None` → autogenerate impossible.
- `env.py:11` strips `+asyncpg` → psycopg2 dialect; psycopg2 in NO requirements file. `compose.yaml:24-34` gates bot+dashboard on migrate success.

Database
- `database.py:5-7` engine pool 5+2, no pre_ping/recycle/timeout. `get_db` annotated wrong; no rollback on exception.
- **Autobegin conflict**: `Depends(get_db)` cached per request; `get_current_user` runs 2 SELECTs (`auth.py:124,135`) → txn open → later `async with db.begin()` raises `InvalidRequestError`. Sites: `main.py:70`, `admin.py:79,106,133,163`, `integrations.py:137,172`, `auth.py:88` (after `routers/auth.py:88/151` SELECT), `auth.py:98`, `seed.py:35` (after `:21`, inside lifespan).
- No raw SQL in dashboard/. Bot uses bound params.

Dead / unfinished
- `schemas.py:76-84 SessionUser` unused. `auth.py:7 Cookie` unused; `auth.py:78` local re-import of `Session`. `auth.py:117-121` `user_id` computed, unused.
- `integrations.py:30` `SUPPORTED_PROVIDERS` vs `:37-42` mapping parallel lists.
- Logout 302 vs fetch caller.
- `IntegrationOut.connected_by` raw UUID never rendered.
- No global exception handler; every router → 500 "Internal error" masking 409/txn bugs. `seed.py` no try. `routers/auth.py:77` assumes `userinfo` parsed.
- CI never lints/tests/builds dashboard.

Scripts / deploy
- `bootstrap.py` copies .env.example, chmod 600, non-destructive; generates no secrets (`POSTGRES_PASSWORD=changeme`, `SECRET_KEY=` empty).
- `update-pi.sh` flock + pull + up bot only; dashboard/frontend/migrate never refreshed.
- `dobby-update.service` root, `/opt/dobby` hardcoded, runs checkout script every 5 min.
- `check_secrets.py` reads every file fully, no size cap; no `*.sql` pattern.
- `scripts/link_google.py` deleted; no refs.
- `dashboard/Dockerfile` root user, `COPY . ./dashboard/`, no `.dockerignore` → `__pycache__` baked; compose gives dashboard none of bot's hardening; `8000:8000`.

### A.3 Frontend + infra

Contract (`api.ts`)
| api.ts | Call | Backend |
|---|---|---|
| :26 me | GET /me | main.py:54 |
| :27 updateMe | PATCH /me {calendar_email} | main.py:59 |
| :29 logout | POST /auth/logout → void | auth.py:178 returns 302 |
| :33 integrations.list | GET /integrations | integrations.py:56 admin |
| :34 connectUrl | `<a href>` GET .../connect | integrations.py:74 |
| :35 disconnect | DELETE /integrations/{p} | integrations.py:155 |
| :40 users.list | GET /admin/users?limit=200 → .items | admin.py:30 |
| :41 users.create | POST /admin/users | admin.py:54 |
| :48 users.update | PATCH /admin/users/{id} | admin.py:115 |
| :50 users.remove | DELETE /admin/users/{id} | admin.py:90 |
| :51 users.setRole | PATCH .../role | admin.py:146 |
| :54 admin.audit | GET /admin/audit?... → .items | admin.py:179 |
- `dashboard/layout.tsx:10` SSR `GET {API}/me` forwarding ALL cookies. `login/page.tsx:32,44` links to `{API}/auth/*`. `/health` never called.
- Logout: fetch follows cross-origin 302 → HTML → `res.json()` throws → swallowed `nav.tsx:35-42`.
- `total` discarded (`api.ts:40,60`); users capped 200, no paging UI; audit uses `entries.length === PAGE_SIZE` heuristic (`audit/page.tsx:60,178`).
- `POST /admin/users` role unvalidated.
- No token in JS (zero localStorage/sessionStorage/document.cookie). `credentials:'include'`.
- 401: `providers.tsx:14` `.includes('401')` never matches real detail text; no client 401→login redirect.
- API URL: `NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'` duplicated `api.ts:3`, `layout.tsx:6`. `frontend/Dockerfile:12` default bakes localhost. `compose.yaml:110-119` same value for build arg AND runtime → SSR inside container hits itself → `/login` redirect loop unless `API_URL` reachable from inside container.

Route protection
- Only gate: `dashboard/layout.tsx:21-32` server-side. `app/page.tsx:6-12` cookie-presence only.
- `admin/users`, `admin/audit` NO role check; 403 → empty table. `integrations/page.tsx:110,137` client-only check.
- `nav.tsx:23-29` links; no dangling links to deleted pages (diff removed contacts/settings). `/dashboard/integrations` still URL-reachable by students.

Pages
- `/login`: OAuth 403 shows raw FastAPI JSON on API origin.
- `/dashboard`: `me` query error ignored → "Welcome back, there."
- `/dashboard/integrations`: `isLoading || !me` → permanent skeletons on failing me; no error UI; no disconnect confirmation.
- `/dashboard/admin/users`: list/remove/role errors never rendered; delete no confirmation (`:305-313`).
- `/dashboard/admin/audit`: no error UI.
- No `dangerouslySetInnerHTML`, no secrets rendered, no TODO/stub handlers.

Types drift (`types.ts`)
- `User.display_name: string` vs Optional BE; rendered `nav.tsx:54`, `users/page.tsx:271,206`.
- `User.created_at: string` vs Optional → `new Date(null)` `users/page.tsx:283`.
- `User.role` union vs free str.
- `Integration.connected_at: string` vs Optional → `integrations/page.tsx:81`.
- `AuditEntry.status: string` vs Optional.
- `Paginated.total` never consumed. `UserEdit` optional fields but dialog always sends all three.

Infra
- compose.yaml: postgres no host port + healthcheck; migrate; bot fully hardened, no ports; dashboard `8000:8000` 0.0.0.0, root, no healthcheck/hardening, env carries all secrets; frontend `3000:3000`, `depends_on` no condition. No bind mounts, no docker.sock. `ENV` never passed.
- compose.registry.yaml: bot only; `DOBBY_IMAGE` not in .env.example.
- compose.test.yaml: bot-only tests/lint.
- No surviving refs to deleted files in yaml/md/py/sh.

CI/CD
- ci.yml: python only (`requirements.txt`, not dashboard's), ruff `bot scripts tests`, pytest, compose config, docker build `calendar-bot:test` (stale name), test image run. Frontend never built/linted/type-checked. Dashboard uncovered (`dashboard/models.py:13` F401 would flag). Actions on floating tags. No `pull_request_target`.
- publish.yml: bot image only; GITHUB_TOKEN only.

Docs vs reality
- `docs/FRONTEND_ARCHITECTURE.md:5,34,95` settings page; `:116-117` claims lockfile + public committed (both `??`).
- `docs/DASHBOARD_ARCHITECTURE.md:4-5,18` guild settings; dup rows `:52/53,63/64,103/104`; `:98` documents ENV; `:112` admits compose lacks it.
- `docs/PRD.md` wholesale stale (GitHub tool, /ask /issue /note /contacts /admin_users /admin_settings, per-user integrations, dropped tables, conversation memory).
- `DOBBY_IMAGE` in DEPLOYMENT.md:95/README:249, not .env.example.
- `bot/config.py:35` mount comment stale.
- README:316 says install dashboard reqs before pytest; nothing imports dashboard.
- ARCHITECTURE.md:83 omits dashboard/frontend CI exclusion.

.gitignore / .dockerignore
- No `*.sql` while README:248/DEPLOYMENT.md:47 say `pg_dump > backup.sql` in repo root.
- `frontend/public/`, `package-lock.json` untracked not ignored.
- root `.dockerignore` doesn't exclude frontend/dashboard dirs (context bloat, nothing baked).
- No `frontend/.dockerignore` → `COPY . .` copies host node_modules/.next. No `dashboard/.dockerignore`.

---

## Appendix B — Phase 3 runtime verification (2026-09-17, Docker Desktop 29.5.3, Python 3.12 venv, SQLAlchemy 2.0.54, FastAPI 0.141.1)

Placeholder credentials only; separate compose project `dobbyresearch` + standalone `postgres:16-alpine` on :55432. Logs in session scratchpad `logs/phase3-*.txt`.

| # | Check | Result | Conf. |
|---|---|---|---|
| B1 | `docker compose run --rm migrate` (compose.yaml as shipped) | **exit 1: `ModuleNotFoundError: No module named 'psycopg2'`** at `migrations/env.py` → `engine_from_config`. `bot` and `dashboard` depend on `migrate: service_completed_successfully` → stack never starts. | R |
| B2 | venv from `requirements.txt` + `dashboard/requirements.txt` | psycopg2 / psycopg absent. B1 is not a Docker artifact. | R |
| B3 | Fresh-clone frontend build (tracked files only) | `docker build` fails at `frontend/Dockerfile:4 RUN npm ci` (no lockfile in git). | R |
| B4 | SQLAlchemy `begin()` after `execute()` on same AsyncSession | `InvalidRequestError: A transaction is already begun on this Session.` | R |
| B5 | Dashboard startup with `BOOTSTRAP_ADMIN_EMAIL` set (compose requires it) on empty DB | `seed.py:35` raises B4 inside lifespan → `Application startup failed. Exiting.` Dashboard cannot boot in the shipped config. | R |
| B6 | Dashboard with seed skipped, forged admin session; mutation routes | `PATCH /me` 500, `POST /admin/users` 500 (valid and `role=banana`), `PATCH /admin/users/{id}` 500, `PATCH .../role` 500, `GET /integrations/notion/callback` 500; all `InvalidRequestError`, all surfaced as `{"detail":"Internal error"}`. Reads work (`/me`, `/admin/users`, `/integrations` 200). | R |
| B7 | `POST /auth/logout` | 302 + log `Error deleting session during logout` (B4 in `delete_session`). **Session row survives; same cookie still returns 200 on `/me` after logout.** | R |
| B8 | Frontend + dashboard containers, compose default `NEXT_PUBLIC_API_URL=http://localhost:8000`, valid cookie | `GET /dashboard` → **307 → /login**; same for `/dashboard/admin/users`. SSR fetch targets the frontend container itself. Out-of-the-box Docker deployment shows the dashboard to nobody. | R |
| B9 | Container users | dashboard `whoami` = **root**; frontend `nextjs`; bot `10001`. | R |
| B10 | Migration 002 replay: users `Maya Chen`, `Jon  Park` (double space); contacts g1+g2 `maya chen`, g1 `jon park`, g1+g2 `new person` | Maya got g1 email (g2 silently dropped, nondeterministic); `Jon  Park` unmatched → duplicate `Jon Park` user; `New Person` inserted twice. | R |
| B11 | `Agent.run` event-loop blocking: two concurrent runs, fake Gemini 2 s each | wall **4.0 s** (serialized); 100 ms heartbeat task stalled **4.02 s**. Whole bot freezes for every Gemini call. | R |
| B12 | `ConfirmView` double-press by requester before first completes | `interaction_check` passes both; **`execute()` ran 2× for one draft** → double publish possible. Stranger correctly rejected. | R |
| B13 | Bot test suite | 104 passed (one test needs writable pytest basetemp; env-only). | R |
| B14 | `ruff check dashboard migrations` (not in CI) | 9 errors: F401 `auth.py:7 Cookie`, `models.py:13 DeclarativeBase`, `routers/auth.py:17 get_current_user`; E402 ×6 in migrations. | R |
| B15 | `frontend`: `tsc --noEmit`, `next lint` | clean. `next lint` rewrote `tsconfig.json` (reverted). | R |
| B16 | Reflected path param | `GET /integrations/evil'provider/callback` → 400 `Unsupported provider 'evil'provider'` (JSON on API origin, not rendered by React). | R |

---

## Appendix C — Phase 2 seam traces (static, cross-referenced)

### C.1 frontend ↔ dashboard (NEW / corrections to A.3)
- REFUTED as live: nullable-in-BE fields (`display_name`, `created_at`, `connected_at`, `status`) are NOT NULL in DB (001:25,28,47,85) → never null on reads. `new Date(null)` = epoch, not "Invalid Date".
- 422 bodies: FastAPI `detail` is a LIST → `api.ts:13 new Error(array)` → UI shows **`[object Object]`** (`dashboard/page.tsx:67`, `users/page.tsx:146,241`). Reachable: `<Input type=email>` accepts `a@b`, BE regex needs a dot; Add/Edit dialogs have no `<form>` → no native validation.
- Logout: cross-origin 302 → browser CORS-blocks the redirect hop → `fetch` rejects `TypeError`. Cookie delete header still applied. (Runtime B7: server row NOT deleted.)
- Unhandled exceptions (outside routers' try, e.g. in `get_current_user`) go through `ServerErrorMiddleware` outside `CORSMiddleware` (`main.py:36`) → no ACAO → browser sees `Failed to fetch`.
- `api.ts:8` sends `Content-Type: application/json` on GET/DELETE → every call preflighted.
- Audit **status vocabulary mismatch**: bot writes `ok|error` (`agent.py:127`, `confirm.py:70`); FE badge expects `success|failed|pending` (`audit/page.tsx:32-37`), filter offers `success|error|pending` (`:98-100`), BE exact `==` (`admin.py:196`) → "Success"/"Pending" filters always empty; successes render grey "Ok".
- Audit `tool` filter exact-match vs substring placeholder; refetch per keystroke.
- 201 users: header "200 users"; oldest (bootstrap admin) invisible. Exactly 25 audit rows: "Next" leads to empty "Page 2".
- Null clearing via `exclude_unset` works. "Nothing to update" 422 branches unreachable from UI.
- Student on `/dashboard/admin/users`: 403 retried 2× → "No users in the allowlist." + live "Add user" button. `/admin/audit`: "No audit entries found." Silent.
- Self-demote succeeds; SSR Nav keeps admin links until reload; later admin calls 403 → empty tables.
- `app/page.tsx` stale cookie → `/dashboard` → `/login` (2 hops); stale cookie never cleared for 7 days. `/login` has no already-signed-in redirect.
- Deployment matrix: `docker compose up` defaults = BROKEN (B8). Host `npm run dev` + Docker dashboard = only zero-config combo that works. Separate registrable domains = BROKEN (Lax cookie). Reverse proxy path-split: `url_for` lacks `--root-path`/proxy headers → OAuth redirect_uri mismatch. `DASHBOARD_URL` trailing slash breaks CORS silently.

### C.2 dashboard ↔ bot ↔ Composio ↔ Postgres (NEW / corrections to A.1–A.2)
- Bot touches only `users` (read display_name/calendar_email/discord_id/id; write calendar_email) and `agent_actions` (insert). Write-only columns nobody reads: `users.added_by`, `sessions.provider`, `sessions.created_at`, `integrations.composio_entity_id`, `integrations.connected_by`, `agent_actions.user_id/guild_id/channel_id`.
- **`agent_actions.user_id` FK (001:78, no ondelete) + ORM without FK (models.py:86-88)** → any user who ever triggered a tool call cannot be deleted (FK error → 500). `users.added_by` FK likewise.
- `UserUpdate.display_name: null` → NOT NULL violation → 500; whitespace-only display_name stored verbatim.
- `integrations.connected_at` never refreshed on reconnect; `composio_entity_id` stored at connect time, never compared to live env.
- `COMPOSIO_ENTITY_ID` divergence: bot `.strip() or "dobby"` vs dashboard raw; bot loads `.env` via dotenv, dashboard has no dotenv.
- Not-connected provider: `check_connected_accounts=False` → Gemini always sees the tools; call fails → `{"success":False,"error":str(exc)}` incl. entity id → Gemini may echo to user.
- Dashboard "Connected" row = "an admin hit the callback URL"; zero coupling to bot behaviour either direction.
- Audit attribution: `discord_id` written verbatim by admin (no trim, FE no `.trim()`) → ` 123`/`<@123>` → `user_id` NULL AND `/email` says not registered AND mention resolution fails AND Discord OAuth login 403. `user_id` read by nobody but FK blocks deletes.
- `calendar_email` validation identical in bot and dashboard. Gate asymmetry: `/email` needs env allowlist AND a `users` row; `PATCH /me` needs only a session. **Two disjoint allowlists** (env `ALLOWED_*` vs `users` table) never synced.
- Migration 002: `name_key` came from HEAD `bot/memory.py:48-49,72-83` (`" ".join(split()).lower()`), not `bot/contacts.py`. PG `lower()` doesn't collapse whitespace → duplicate directory users (B10). Multi-guild same key → nondeterministic email (B10). First-name-only contact "Maya" inserted as user → `find_user_by_name("Maya")` exact-matches the contact row before the member's own. `DELETE FROM integrations` wipes rows; Composio connections persist. Downgrade restores no data.
- `find_user_by_name` fuzzy ≥0.6 false positives: jon→john .857, sam→sami .857, dan→dana .857, ana→anna .857, sara→sarah .889, alex→alexa .889, tom→tim .667, ben→bea .667, lee→leo .667, leo→leonard .600 → calendar invite to wrong person. `seed.py:29` display_name = email local part becomes a candidate.
- Injection/leak: `display_name` (unbounded, no newline strip) → system prompt (`agent.py:73-76`), request text (`context.py:58-60`), tool result (`google_calendar/tools.py:14-19`). Full emails of @mentioned users go into the system prompt → any allowlisted member can ask Dobby for someone's email; `mask()` only on `/email show`.
- Lifecycle: `update-pi.sh` refreshes bot only; `migrate` image is the stale local build. On `UndefinedColumnError`: `record_action` at `agent.py:121-129` is outside try → **tool already executed at `:118` before audit INSERT raises** → event/page created, user told "failure", retry → duplicates. Confirm path wraps audit in try — asymmetric. No startup schema check.
