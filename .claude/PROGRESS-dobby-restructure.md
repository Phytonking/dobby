# Dobby restructure: what was done (2026-09-10)

Companion to `PLAN-dobby-restructure.md`. All five phases are implemented, each as one
commit on `main`, and the suite is green (121 tests, `ruff check` and `ruff format` clean).

| Phase | Commit | Summary |
|---|---|---|
| 1 Context-first scheduling | `d247355` | Mentions always gather up to 12 recent messages (with author names) plus channel/thread names and pass them to Gemini, which is told to infer a title from them before clarifying. |
| 2 Response pools | `37498a1` | `bot/voice.py` `say(key, **fields)` reads `bot/responses/<key>.txt` on every call, picks one line at random, and keeps nothing cached. 32 keys, 20 phrasings each. |
| 4 Reaction confirmation | `7f8df00` | Buttons and the `calendar-preview.txt` attachment are gone. Dobby posts the full preview inline, adds 🟢/🔴 to its own message, and `on_raw_reaction_add` acts on the requester's reaction (2-minute expiry, re-authorization, one write max). |
| 3 Invitees + contact memory | `07d3e62` | `bot/contacts.py` stores name -> email in `data/contacts.json` (atomic writes, lock). Gemini returns `invitees`; unknown names make Dobby ask in-channel, and a reply to that question saves the email and re-runs the original request. `/contacts add|list|remove` added. Compose mounts `./data:/data`. |
| 5 Delete/update by title | `d70c9ea` | `bot/lookup.py` fuzzy-matches `target_title` against the next 60 days of editable events. Clear match -> preview asking "is this the right meeting?"; no title -> Dobby asks; several -> numbered list, reply with a number. |

Phase order was 1 -> 2 -> 4 -> 3 -> 5 as the plan specified (4 before 3 because 3 and 5 build on the follow-up mechanism and reaction confirmation).

## Key code paths for whoever picks this up

- `bot/main.py`
  - `on_message` -> `pending_followup` decides whether a message is a reply to one of Dobby's questions (`self.awaiting[(channel_id, user_id)]`, `Followup` dataclass, 5-minute expiry) or a fresh mention, then calls `handle_request`.
  - `handle_request` catches `NeedTitle`, `ChooseEvent`, `NeedContacts` from the scheduler and turns each into a question + `Followup`; otherwise it calls `present`, which edits the preview inline and arms 🟢/🔴.
  - `on_raw_reaction_add` / `finish` / `expire_after` handle confirmation; `PendingConfirmation` is keyed by message id in `self.confirmations`.
  - `member_allowed(user, channel, mention=...)`: slash-command previews skip the MENTION_CHANNEL_IDS rule when reauthorizing.
- `bot/service.py` `Scheduler.prepare(request, event_id, interaction_id, history, place, title)` raises the three follow-up exceptions (all `UserError` subclasses, so `/schedule` still gets readable text).
- `bot/models.py`: `Plan.invitees`, `Plan.target_title`, `is_editable(event)`, `event_body(..., emails)` merges attendees with existing guests because PATCH replaces the array.
- `bot/voice.py`: `FIELDS` lists the placeholders each key may use; `FALLBACK` is used if a file is missing. `tests/conftest.py` pins `pick()` to the first line so wording assertions are stable.

## Follow-up change (same day): titles and confirmation labels

- Event titles are now `<thread or channel name> | <event name>` (`models.titled`, applied in `event_body` for creates and renames; never prefixed twice). The `/schedule` slash command now also passes its channel as `place`.
- The 🟢 confirmation shows `<title>, <month>/<day>` in the team timezone (`models.event_label`) instead of the event ID. `main.plain_title` keeps the pipe unescaped.
- Quotes in the user's example were read as placeholder markers, so no literal quote characters are written into titles.

## Follow-up change: structured messages and update diffs

- `bot/format.py`: readable times (`Sat Oct 12, 2030 · 2:00 PM – 3:00 PM (UTC-06:00)`) and `changes(existing, body, zone)` producing `Label: old → new` lines for title, description, location, time span and added invitees.
- `preview(proposal, zone, note)` now emits bold labels (`**Title:**`, `**When:**`, `**Invitees:**`…), a `**Changes:**` bullet list for updates, and the context note in italics at the end.
- The 🟢 confirmation shows `**Event:** <title>, <m>/<d>` and, for updates, `**Changed:**` bullets.

## Follow-up change: questions and off-topic chat (`bot/chat.py`)

- `Concierge.respond(request, history, place)` -> `("schedule", None)` for the planner, `("capabilities", text)` for "what can you do", or `("chat", text)` for anything else. Regex fast paths avoid a Gemini call for obvious cases; ambiguous messages get one structured Gemini call that classifies and, for chat, writes the reply.
- Chat replies end with a line from the new `farewell` pool ("Dobby is going now…") and create no follow-up state. New pools: `capabilities_intro`, `chat_fallback`, `farewell`.
- `main.py` changes are limited to constructing the concierge, a `converse()` executor hop, and one check in `handle_request` for fresh mentions (`fresh=True`); resumed follow-ups skip it.

## Bug fix: Dobby went quiet after being given an email (2026-09-11)

- Cause: the email-reply branch of `on_message` had no error handling, so any exception (in the field, `PermissionError` writing `data/contacts.json` from a root-owned bind mount) surfaced only as `discord_event_failed event=on_message`.
- Fix: follow-up handling moved into `Bot.route()` and wrapped so failures are reported in the channel; a failed save no longer blocks the meeting (emails from the conversation are passed to `Scheduler.prepare(..., emails=...)` and win over the memory file, with a `contact_not_saved` notice); startup and `--check` now verify the data directory is writable (`check_data_dir`).

## Bug fix: doubled titles and empty "Changed:" lists (2026-09-11)

- `titled()` no longer prefixes a title that equals the thread name, and collapses an existing "X | X" back to "X".
- `event_body()` drops fields Gemini echoed unchanged (title, description, location, identical time span) so the diff shows only real changes; if nothing is left it raises "Nothing would change…" instead of sending a no-op PATCH.
- Confirmations print "(no visible change)" rather than an empty list.

## Follow-up change: concise title prefixes

- `Plan.place_label`: the planner returns a one- or two-word label when the thread/channel name is longer than one word (no extra Gemini call). `models.place_name(place, label)` uses the raw name if it is a single word, else the label (validated to ≤ 2 words / 30 chars), else the first two words of the raw name. The planner is also told to keep `summary` to six words at most.

## Deviations and notes

- The first commit also removed `ComprehensiveTest.txt`; it was already deleted in the working tree before work started, and `git add -A` staged it.
- `preview_attached` from the plan's key list was dropped (no attachments remain); `confirm_instructions`, `confirm_expired`, `contact_unknown` were added.
- Preview shows invitee email addresses in the channel so the requester can verify them; `/contacts list` masks them.
- `python -m bot.main --check` was not exercised end to end here because this checkout has no `.env`.
- `tests/test_docker_config.py` needs `pytest --basetemp=<writable dir>` in this sandbox; the tests themselves pass.

## Still to verify manually (see plan "Verification" items 4-7)

Run against the test guild: title inference in a thread, varied phrasings, the invite -> email prompt -> resume flow, `/contacts`, 🟢/🔴 behaviour and expiry, and the delete-by-title flow with one and with several matches. Bot needs the **Add Reactions** permission (and optionally Manage Messages to clear reactions). Docker: `docker compose up -d --build` should create `./data/contacts.json` on first save.
