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

## Deviations and notes

- The first commit also removed `ComprehensiveTest.txt`; it was already deleted in the working tree before work started, and `git add -A` staged it.
- `preview_attached` from the plan's key list was dropped (no attachments remain); `confirm_instructions`, `confirm_expired`, `contact_unknown` were added.
- Preview shows invitee email addresses in the channel so the requester can verify them; `/contacts list` masks them.
- `python -m bot.main --check` was not exercised end to end here because this checkout has no `.env`.
- `tests/test_docker_config.py` needs `pytest --basetemp=<writable dir>` in this sandbox; the tests themselves pass.

## Still to verify manually (see plan "Verification" items 4-7)

Run against the test guild: title inference in a thread, varied phrasings, the invite -> email prompt -> resume flow, `/contacts`, 🟢/🔴 behaviour and expiry, and the delete-by-title flow with one and with several matches. Bot needs the **Add Reactions** permission (and optionally Manage Messages to clear reactions). Docker: `docker compose up -d --build` should create `./data/contacts.json` on first save.
