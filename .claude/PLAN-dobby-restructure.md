# Dobby restructure: context-first scheduling, response pools, contact memory

## Context

Dobby (`bot/`) is a Discord bot that turns one mention or `/schedule` call into a single Gemini plan and a Google Calendar write. Three gaps prompted this work:

1. **Context is opt-in and thin.** `bot/main.py:248-258` only reads channel history when the request contains words like "this/that/above", sends only `{text, at}` (no author, no channel or thread name), and Gemini is told to `clarify` whenever a title is missing. So "@Dobby schedule a meeting for tomorrow at 3" in a thread called "Q4 launch prep" asks the user for a title instead of proposing one.
2. **Dobby's voice is ~25 hardcoded strings** spread through `bot/main.py` (preview, confirmation, cancel, cooldown, errors, help). Every situation has exactly one sentence.
3. **No attendees, no memory.** `event_body()` never emits `attendees`, the planner is instructed to clarify on any invitation request, and the repo has zero application persistence (`ARCHITECTURE.md:96-102`). Users cannot say "invite Maya".

Outcome: Dobby gathers channel context before deciding to clarify, picks from 20 phrasings per situation loaded from text files on demand, and can invite named people, learning their emails once and remembering them in a file.

Decisions already made with the user:
- Email capture = reply to Dobby's prompt (or mention with "Maya is x@gmail.com") **plus** a `/contacts` slash command.
- Contacts persist in `./data/contacts.json`, exposed to Docker through a writable `./data` bind mount.

---

## Phase 1: Context-first scheduling

Goal: before Gemini is ever asked to clarify, it sees the recent conversation, who said it, and the channel/thread name.

### Changes

**`bot/main.py` `on_message` (lines 247-267)**
- Remove the keyword gate (`use_context = re.search(...)`). Always collect history for mention requests, bounded by `config.context_limit`, still skipping bots and empty content, still requiring the requester's `read_message_history` permission (fall back to empty history rather than raising if they lack it).
- Enrich each history row: `{"author": prior.author.display_name, "text": ..., "at": ...}`.
- Build a `place` dict: `{"channel": parent-channel name, "thread": thread name or None}`. For `discord.Thread` use `message.channel.name` and `message.channel.parent.name`; for a text channel use `message.channel.name` and `None`.
- Pass `place` through `Scheduler.prepare` -> `Planner.plan`.
- Replace the trailing "Used N recent text messages" line with a response-pool key (Phase 2); until then keep the line but make it unconditional.

**`bot/service.py` `Scheduler.prepare`** — add `place=None` parameter, forward to `self.planner.plan(request, existing, history, place)`.

**`bot/planner.py`**
- `plan`/`_plan` accept `place`. Add `"place": place or {}` to the JSON `contents`.
- Extend the system instruction (keep the untrusted-data sentence):
  - "recent_messages include author names; place gives the channel and thread names. Use both to infer a concise meeting title when the request omits one, preferring the thread name or the topic under discussion. Only clarify for a title when neither the request, the conversation, nor place suggests one."
  - Keep "never invent date/time"; date and start still require clarification when absent.
- Raise `context_limit` default in `bot/config.py:25,63` from 6 to 12 (env override `CONTEXT_LIMIT` already parsed there; verify name).

**Tests** — `tests/test_mentions.py`: update the existing history test so context is always fetched; add a case asserting `place` and `author` reach the planner. `tests/test_planner.py`: assert `place` is in the request body sent to Gemini.

**Docs** — `README.md` privacy paragraph and `/calendar_help` text (`bot/main.py:417`): recent messages from the channel are always sent to Gemini for mention requests (slash `/schedule` still sends none).

---

## Phase 2: Response pools (20 phrasings per situation)

Goal: every Dobby-voice sentence comes from a text file with 20 alternatives, chosen at random, loaded from disk when needed and not held in memory afterward.

### New module `bot/voice.py`

```python
RESPONSE_DIR = Path(__file__).parent / "responses"

def say(key: str, **fields) -> str:
    # Read responses/<key>.txt, pick one non-empty, non-comment line, format, return.
    # No module-level cache: the list is local to the call and garbage-collected on return.
    # Missing file or missing placeholder -> log warning, return a fixed fallback for that key.
```

- Lines use `str.format` placeholders (`{event_id}`, `{count}`, `{days}`, `{question}`, `{timezone}`). Use `string.Formatter().parse` once per line to validate placeholders against `fields`; skip lines that reference unknown fields.
- Add `responses/` to the package data (`pyproject.toml`; check `Dockerfile` COPY includes `bot/` wholesale — it does via the package copy, verify).

### Response keys (one `.txt` each, 20 lines each)

Mention flow: `working`, `preview_intro`, `preview_outro`, `preview_attached`, `context_used`, `permission_missing`, `cooldown`, `not_authorized`, `busy`.
Confirmation: `created`, `updated`, `deleted`, `cancelled`, `confirm_unusable`, `apply_failed_suffix`.
Errors: `needs_help` (prefix before a `UserError`, includes `{question}`), `generic_failure`.
Slash: `events_found` (`{count}`, `{days}`), `events_none`, `help_intro`.
Contacts (Phase 3): `ask_email` (`{name}`), `contact_saved` (`{name}`), `contact_invalid_email`, `contact_removed`, `contacts_empty`, `contacts_list_intro`.

Keep Dobby's third-person house-elf voice; keep the two facts that must survive in every variant of `preview_outro` (confirm within 2 minutes; times include UTC offset) and in `created/updated/deleted` (the event ID is appended by code, not by the text).

### Wire-up in `bot/main.py`
- `preview()` -> `say("preview_intro")` / `say("preview_outro")`; structured lines (Title/Start/End) stay literal.
- `Confirmation.confirm/cancel/interaction_check` -> `say("created"|"updated"|"deleted"|"cancelled"|"confirm_unusable")`.
- `Bot.error` -> `say("needs_help", question=safe(str(exc))[:1500])` and `say("generic_failure")`.
- `gate`, `take_cooldown` callers, `on_message` placeholder and Forbidden handler, `/events`, `/calendar_help` -> corresponding keys.
- `UserError` messages raised in `models.py`/`service.py`/`calendar.py`/`planner.py` remain literal: they are precise diagnostic text, and `needs_help` already wraps them in Dobby's voice.

### Tests
- `tests/test_voice.py`: every file in `responses/` has exactly 20 usable lines; every line formats cleanly with the documented fields for that key; `say()` with a bogus key returns the fallback and does not raise; two calls with a seeded `random` return different lines.
- Existing tests that assert on exact Dobby strings (`test_discord.py`, `test_mentions.py`) switch to asserting on the key's fallback or on `unittest.mock.patch("bot.main.say")`.

---

## Phase 3: Invitees and contact memory

Goal: "invite Maya and Leonard" adds attendees; unknown names trigger an email prompt whose answer is saved to `data/contacts.json` and reused.

### 3a. Contact store — new `bot/contacts.py`

```python
class Contacts:
    def __init__(self, path): ...
    def lookup(self, names) -> tuple[dict[str,str], list[str]]  # (found name->email, missing names)
    def save(self, name, email)   # case-insensitive key, atomic write (tmp + os.replace)
    def remove(self, name)
    def all() -> dict
```
- File format: `{"contacts": {"maya": {"email": "...", "display": "Maya", "added_by": <discord id>, "at": iso}}}`. Guild-scoped by construction (one guild per bot).
- Email validation: simple `^[^@\s]+@[^@\s]+\.[^@\s]+$`; reject Discord mentions.
- `bot/config.py`: new `data_dir` (env `DOBBY_DATA_DIR`, default `./data`); `contacts_file = data_dir/contacts.json`. `scripts/bootstrap.py` creates `data/`. `.gitignore` adds `data/`.
- `compose.yaml`: add `volumes: - ./data:/data` and `DOBBY_DATA_DIR: /data`; keep `read_only: true` for the rest of the rootfs. `scripts/bootstrap.py` handles ownership for uid 10001 the same way it repairs `secrets/`.

### 3b. Planner and model

- `bot/models.py` `Plan`: add `invitees: list[str] = Field(default_factory=list, max_length=20)` (names or emails as written by the user), and a new action value `"need_emails"` is **not** added; the planner just returns names and the service resolves them.
- `bot/planner.py` system instruction: remove "invitations/attendee changes" from the clarify list; add "Put people to invite in invitees as the names or emails the user wrote. Do not guess email addresses."
- `bot/models.py` `event_body`: when `plan.invitees` resolve to emails, emit `attendees=[{"email": e} for e in emails]`. For `update`, merge with `existing["attendees"]` (PATCH replaces the array; `docs/RESEARCH.md` already warns about this), de-duplicated by email.
- `bot/service.py` `Scheduler.prepare`: after planning, `found, missing = contacts.lookup(plan.invitees)` (strings containing `@` pass through as-is). If `missing`, raise new `NeedContacts(missing, pending_request)` exception (subclass of `UserError` so slash-command paths still get readable text).
- `Proposal` gains `attendees: list[str]` for the preview; `preview()` lists "Invitees: a@x, b@y".
- `tests/test_scheduler.py:58` (`"attendees" not in body`) becomes: no attendees when none requested; correct attendees when requested.

### 3c. Conversational email capture in `bot/main.py`

- On `NeedContacts` in `on_message`: reply with `say("ask_email", name=first_missing)` listing all missing names, and store a pending entry in a new in-memory dict `self.awaiting_emails[(channel_id, user_id)] = {"names": [...], "request": original text, "history": ..., "place": ..., "expires": monotonic+300, "prompt_id": reply.id}`.
- Extend the `on_message` gate: accept a message from the same user in the same channel if **either** it mentions Dobby **or** it is a `message.reference` reply to the stored `prompt_id`. Parse `Name is email`, `Name: email`, `Name - email`, or a bare email when exactly one name is outstanding. Save each pair via `contacts.save`, reply `contact_saved`, and if no names remain, re-run the original request automatically (same `prepare` path, same cooldown bypass for this resume). Invalid/unparseable -> `contact_invalid_email` and keep waiting.
- Prune expired entries in the same sweep as `take_cooldown`.

### 3d. `/contacts` slash command

`/contacts action:{add,list,remove} name:str email:str|None`, guild-only, goes through `gate`. `add` requires a valid email; `list` replies ephemerally with names only (emails masked as `m***@gmail.com`) to keep addresses out of the channel; `remove` deletes. Reuse `Contacts` methods; all replies through `say()`.

### 3e. Docs
- `README.md` Limitations line 290 and `ARCHITECTURE.md:96-102` "no conversation memory": update to describe `data/contacts.json`, the 5-minute pending-email window, and that invitees receive Google invitations (`sendUpdates=all` already set in `Calendar.apply`).
- `/calendar_help`: add an "invite Maya and Leonard" example.

---

## Phase 4: Inline preview with 🟢/🔴 reaction confirmation

Goal: no `calendar-preview.txt` attachment and no buttons. Dobby posts the full details in its own message, adds 🟢 and 🔴 reactions to it, and acts when the requester picks one.

### Changes in `bot/main.py`
- Delete the `discord.ui.View`-based `Confirmation` class. Replace with a plain `PendingConfirmation` dataclass: `owner`, `proposal`, `origin_channel`, `message_id`, `expires` (monotonic + 120), `used`, `kind` (`"apply"` for calendar writes, `"pick_event"` for Phase 5). Keep in `self.confirmations: dict[message_id, PendingConfirmation]`; prune expired entries in the same sweep as `take_cooldown` and edit the expired message to a `say("confirm_expired")` line.
- Intents: `intents.guild_reactions = True`. Bot needs the Add Reactions permission; extend the Forbidden handler text and README permission list.
- After sending/editing the preview message: `await msg.add_reaction("🟢")`, `await msg.add_reaction("🔴")`, then register the pending entry. For `/schedule`, get the message via `await interaction.original_response()`.
- New handler `on_raw_reaction_add(payload)` (raw, because `max_messages=None` means no message cache): ignore the bot's own reactions; look up `payload.message_id`; require `payload.user_id == owner`, not used, not expired; re-run `member_allowed(owner, origin_channel)` exactly as `interaction_check` did; then on 🟢 run `self.work(self.calendar.apply, proposal)` and edit the message with `say("created"|...)`; on 🔴 edit with `say("cancelled")`. Set `used = True` before the first await. Other emoji are ignored. Try `msg.clear_reactions()` after the decision; if Forbidden (needs Manage Messages), fall through silently since the entry is already marked used.
- Preview content: keep everything inline. If `preview()` exceeds 1900 chars, send the remainder as a second reply message rather than attaching a file. Drop `preview_attached` from the Phase 2 key list; add `confirm_expired` and `confirm_instructions` ("React 🟢 to confirm or 🔴 to cancel within 2 minutes").
- `/events` keeps its `events.json` attachment: the user's request concerns confirmation previews only.

### Tests
- `tests/test_discord.py`: replace button tests with `on_raw_reaction_add` tests using a fake `RawReactionActionEvent` (`emoji.name`, `user_id`, `message_id`, `channel_id`): owner+🟢 applies once, second 🟢 does nothing, non-owner ignored, 🔴 cancels without a calendar call, expired entry does nothing.
- Remove attachment assertions from `test_mentions.py`.

## Phase 5: Resolving which event to delete (or update) without an event ID

Goal: "delete the sync meeting" works from a mention. Dobby first looks at the conversation for the referenced event, otherwise asks for the title, searches the calendar for similar titles, and asks the user to confirm the best match before deleting.

### Planner (`bot/planner.py`, `bot/models.py`)
- `Plan` gains `target_title: str | None` (max 200). System instruction: "For update or delete without a selected event, set target_title to the meeting the user means, taken from the request or, if the request only says 'that meeting', from recent_messages or place. Leave target_title empty and ask clarify only when nothing identifies the meeting."
- Phase 1 already delivers `history` and `place`, so context is read before any clarification.

### Resolver (new `bot/lookup.py`)
```python
def find_events(calendar, title, timezone, days=60) -> list[tuple[float, dict]]
    # calendar.list(now, now+days) filtered to active, single, timed, default events
    # (same predicate as Scheduler.prepare lines 25-31, extract it into models.is_editable(event)),
    # scored by difflib.SequenceMatcher(None, title.lower(), summary.lower()).ratio(),
    # also full credit when title is a substring of summary; return sorted desc, ratio >= 0.5.
```

### Service (`bot/service.py`)
- In `prepare`, when `plan.action in ("update","delete")` and no `event_id`:
  - `target_title` empty -> raise `NeedTitle(pending_request)` (same pattern as `NeedContacts`).
  - Otherwise `matches = find_events(...)`. No match -> `UserError` via `say("no_matching_event", title=...)`. One clear winner (top ratio >= 0.8 and at least 0.15 above the runner-up) -> proceed with that event as `existing`, mark `Proposal.resolved_by_title = True`. Several close matches -> raise `ChooseEvent(candidates[:3], pending_request)`.
- The Phase 3 in-memory pending-reply mechanism generalizes: `self.awaiting[(channel_id, user_id)] = {"kind": "emails"|"title"|"choose", ...}`.
  - `kind="title"`: Dobby asks `say("ask_title")`; the user's reply (reply-to-prompt or mention) becomes `target_title` and the original request re-runs with `event_id` resolved.
  - `kind="choose"`: Dobby lists up to 3 candidates numbered with title and start; the user replies `1`/`2`/`3`; the chosen event ID is injected and the request re-runs.
- When `resolved_by_title` is set, `preview()` leads with `say("confirm_event_match", title=..., start=...)` ("Dobby found this meeting. Is it the right one?") so the 🟢/🔴 step from Phase 4 doubles as the "is this the correct event" confirmation. No extra round-trip when there is a single strong match.
- Slash `/schedule` without `event_id` gets the same resolver, but a `NeedTitle`/`ChooseEvent` there surfaces as a readable `UserError` asking to add the title or an `event_id` (no reply-based follow-up on interactions).

### Response keys added
`ask_title`, `choose_event_intro`, `no_matching_event` (`{title}`), `confirm_event_match` (`{title}`, `{start}`).

### Tests
- `tests/test_lookup.py`: scoring picks exact > substring > fuzzy; ignores recurring/all-day/cancelled; threshold behaviour.
- `tests/test_scheduler.py`: delete with `target_title` and one strong match resolves `event_id`; ambiguous raises `ChooseEvent`; missing title raises `NeedTitle`.
- `tests/test_mentions.py`: full flow with a reply of `2` selecting the second candidate.

## Phase order and independence

Order: 1 → 2 → 4 → 3 → 5. Phase 1 and 2 touch the same `on_message` lines. Phase 4 replaces the confirmation class that Phases 3 and 5 both build on, so it goes before them. Phase 3 introduces the pending-reply mechanism that Phase 5 generalizes. Each phase should be a separate commit and keep `pytest` green.

## Verification

1. `python -m pytest` after each phase (all tests are mocked; no network).
2. `ruff check .` (project uses ruff, `pyproject.toml`).
3. `python -m bot.main --check` still passes with and without `DOBBY_DATA_DIR` set.
4. Manual, running locally against the test guild:
   - In a thread named e.g. "Q4 launch prep", post two messages about a topic, then `@Dobby schedule a meeting tomorrow at 3pm`. Expect a preview whose title derives from the thread/topic, no clarification.
   - Trigger the same request three times; confirm the intro/outro sentences vary and remain grammatical.
   - `@Dobby schedule a sync Friday 10am and invite Maya`. Expect an email prompt. Reply to it with `maya@gmail.com`. Expect "saved" and an automatic preview listing the invitee. Repeat the request: no prompt. Confirm the event and check the attendee in Google Calendar and that `data/contacts.json` holds the entry.
   - `/contacts list`, `/contacts remove name:Maya`, then re-run to confirm the prompt returns.
5. Docker: `docker compose up -d --build`, run the invite flow, confirm `./data/contacts.json` appears on the host and survives `docker compose restart`.
6. Reaction confirmation: the preview message shows all details inline with no attachment and carries 🟢/🔴. A different user's reaction does nothing; the requester's 🟢 creates the event; 🔴 edits the message to a cancel line; waiting past 2 minutes yields the expired line.
7. Delete flow: in a channel where "the design review" was discussed, `@Dobby delete that meeting` proposes the matching calendar event with "is this the right one?" and 🟢 deletes it. With no context, Dobby asks for the title, then finds the event by fuzzy title. With two similar titles, Dobby lists them and a reply of `2` selects the second.
