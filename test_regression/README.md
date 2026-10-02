# System regression suite

Run from the repository root:

```sh
python test_regression/run.py
```

Requires Python 3 and Docker with a recent Compose version supporting `!reset`.
Application builds, Postgres, Chromium, and Python test dependencies run in Docker.
No production credentials or connected social accounts are required. The runner
uses explicit dummy settings, an isolated Compose project and database volume,
no published host ports, and an internal network that blocks external traffic.
It removes its own containers and volume on completion, including failures.

The first 20 scenarios retain the original proposed list. The additional 10 focus
on individual image components. Parameterized cases intentionally make the number
of executed tests larger than 30. Names beginning `test_NN_` map to this list.

| # | Image / feature | Passing behavior | Implementation |
| --- | --- | --- | --- |
| 01 | Bot + Dashboard + Frontend + Postgres \| Fresh Docker startup | Production images build; migrations finish; HTTP services and bot become ready without restarts. | `run.py` |
| 02 | Bot Docker image \| Production container restrictions | Non-root, read-only, capability-restricted bot executes a provider/cache operation within configured limits. | `run.py`, `bot_smoke.py` |
| 03 | Bot + Dashboard \| Instagram and LinkedIn disconnected | Calendar/Notion operations and help remain available; missing social configuration reports a useful error. Dashboard usability is also exercised in 04. | `test_bot.py` |
| 04 | Bot + Dashboard + Frontend \| No service accounts connected | Dashboard profile/roster and bot help/email work without connections; browser workflow opens disconnected service-account cards. | `test_bot.py`, `test_dashboard.py`, `test_frontend.py` |
| 05 | Bot integrations \| Individual provider failure isolation | Each failing provider leaves operations for the other providers available. | `test_bot.py` |
| 06 | Bot + Dashboard configuration \| Required and optional settings | CLI rejects invalid required settings without exposing secrets; absent Instagram configuration is accepted. Dashboard login-mode coverage is in 11. | `test_bot.py` |
| 07 | Migration image + Postgres \| Safe database upgrades | Real upgrades from revisions 001 and 002 preserve supported user/session data; repeat upgrades succeed. Revision 002 service connections survive the upgrade to head. | `test_migrations.py` |
| 08 | Bot + Dashboard + Postgres \| Data survives restarts | A saved account/email survives Postgres restart and application-container recreation. | `run.py` |
| 09 | Bot + Dashboard + Postgres \| Database outage recovery | A database-dependent login fails within a bounded time during outage; the stored profile is correct after recovery. | `run.py`, `probe.py` |
| 10 | Frontend + Dashboard API \| Complete user workflow | Chromium logs in, edits an email, reloads it, and opens roster/connections through the production frontend. | `test_frontend.py` |
| 11 | Frontend + Dashboard authentication \| Supported login modes | API login-method responses and local-session acceptance follow local/OAuth/both mode. Local mode requires no OAuth credentials. | `test_dashboard.py` |
| 12 | Dashboard authentication + Postgres \| Session lifecycle | Tampering, logout replay, expiry, and password-reset revocation reject old sessions. | `test_dashboard.py` |
| 13 | Dashboard API \| User and admin authorization | Anonymous requests are denied; members cannot read admin data, manage connections, or alter other users/roles. | `test_dashboard.py` |
| 14 | Bot Discord commands \| Server, user, role, and channel permissions | Real command handlers defer/respond only for allowed requests and never call a provider when denied. | `test_bot.py` |
| 15 | Dashboard + Bot integrations \| Account connection lifecycle | Only verified active connections are recorded; disconnect removes remote accounts before the local record; stale callbacks fail. | `test_dashboard.py` |
| 16 | Bot Instagram + LinkedIn \| Publish confirmation enforcement | Only the requester can confirm; cancelled/expired views reject even queued clicks; the published text matches the preview. | `test_bot.py` |
| 17 | Bot Instagram + LinkedIn \| Duplicate publish prevention | Concurrent/repeated clicks execute once, including ambiguous provider timeout outcomes. | `test_bot.py` |
| 18 | Bot + Dashboard \| Concurrent user isolation | Parallel profile edits remain separate; confirmation ownership and cooldowns remain per user. | `test_bot.py`, `test_dashboard.py` |
| 19 | Bot + Dashboard API + Frontend \| Input validation and safe rendering | Malformed emails/IDs/media URLs are rejected, SQL-like names remain data, and Chromium renders hostile markup as text. | All three behavior test modules |
| 20 | Bot + Dashboard + Postgres audit log \| Secret and private-content protection | Publish failures do not log provider error payloads; audit rows keep metadata, not conversation/tool payload columns; profile responses exclude password hashes. | `test_bot.py`, `test_dashboard.py` |
| 21 | Bot image \| Slash-command registration and guild synchronization | Expected command groups register once and synchronize to the configured guild. | `test_bot.py` |
| 22 | Bot image \| Discord reconnect handling | Repeated readiness events do not duplicate commands or execute provider actions. | `test_bot.py` |
| 23 | Bot image \| Missing message-history permissions | Forbidden history reads return empty context; basic commands remain usable. | `test_bot.py` |
| 24 | Bot + Dashboard images \| Shared email-directory consistency | Dashboard changes are visible to bot lookups; bot writes and cleared addresses are visible to the dashboard. | `test_dashboard.py` |
| 25 | Dashboard image \| Local-login throttling | Five failed attempts are allowed; excess attempts receive 429/Retry-After; attempts resume after the window expires. | `test_dashboard.py` |
| 26 | Dashboard image \| Audit filtering and pagination | Tool/status filters and total counts agree; offsets and bounds work. | `test_dashboard.py` |
| 27 | Dashboard image \| Duplicate-user handling and CRUD | Duplicate email/Discord IDs return 409; create, edit, promote, and delete persist correctly. | `test_dashboard.py` |
| 28 | Dashboard image \| Production session-cookie flags | Production cookies carry Secure, HttpOnly, SameSite, path, and lifetime attributes. | `test_dashboard.py` |
| 29 | Frontend image \| API-unavailable error and recovery | Failed login-method requests show an actionable error; reloading after recovery restores login controls. | `test_frontend.py` |
| 30 | Frontend image \| Protected routes and member navigation | Anonymous dashboard routes redirect to login; members see their profile without admin navigation. Server authorization is separately checked in 13. | `test_frontend.py` |

## GitHub checks

`CI` runs on every pull request and push to `main`, plus manual dispatch. Its
reusable regression job appears as **Regression | Docker, Bot, Dashboard,
Frontend, Postgres**. Failures fail the check. JUnit XML, Compose logs, and browser
failure screenshots are uploaded as `system-regression-results`; a per-case table
is added to the GitHub job summary. Existing unit/lint checks remain separate.
Image publication now depends on both CI jobs succeeding for the same commit.
Use manual dispatch of **CI** to verify and publish `main` manually.

Repository branch protection must select this check to make it a merge
requirement; adding a workflow does not change GitHub repository settings.

## Test boundaries

Postgres, migrations, application HTTP middleware, production image builds, and
Chromium are real. In-process API tests use the real app with a real Postgres
session dependency. Browser tests use the running dashboard and frontend images.
Only outbound provider responses and Discord gateway transport are substituted.
The bot smoke test runs the actual entry point, initialization, command sync,
readiness callback, and provider bridge/cache write under production restrictions.
It cannot prove that real Discord credentials or current third-party APIs work.
No tests assert a model name, prompt wording, or generated answer.

The browser deployment uses one hostname with distinct frontend/API ports,
matching the supported LAN cookie setup. Migration 001-to-002 intentionally drops
legacy personal integration records and conversation storage; this is not a
promise to preserve data that the migration explicitly removes. Database-outage
coverage uses the dashboard's database-dependent login and recovery path; it is
not a full network-partition or load test. URL validation rejects malformed URLs,
credentials and local IP literals; it does not resolve public DNS or follow redirects.
