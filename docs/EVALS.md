# Evaluations

Evaluations test real model behavior against scripted scenarios without actually executing tools or touching Discord/Google Calendar. The agent receives real Model responses, but Composio calls return canned data.

## Quick start

```bash
# Needs .env with MODAL_BASE_URL, MODAL_PROXY_TOKEN_ID, MODAL_PROXY_TOKEN_SECRET, MODAL_MODEL
make eval

# Optional: run specific cases only
python -m scripts.eval --only team-time --only prompt-injection
python -m scripts.eval --db postgresql+asyncpg://user:pass@host:5432/db
```

## What gets tested

Evaluation cases in `scripts/eval_cases.py` cover:

| Category | Examples |
| --- | --- |
| **Scheduling** | Create/update/delete meetings, handle conflicts, preserve duration, timezone handling |
| **Names & invitees** | Look up contacts by name, prompt for missing emails, handle ambiguous matches |
| **Scope & privacy** | Respect channel context, reject out-of-scope requests, no credential leaks |
| **Edge cases** | Null queries, malformed times, missing required fields, duplicate requests |
| **Injection & safety** | SQL injection attempts, prompt injection, hidden instructions in user input |

Each case specifies:
- **User input** (a Discord message or slash command)
- **Channel context** (recent messages, channel name, thread name)
- **Expected tools** (which Composio tools should be called)
- **Expected outcome** (created/updated/deleted/error)

## Running an evaluation

```bash
# Full run (all cases in eval_cases.py)
make eval

# Specific cases
python -m scripts.eval --only team-time
python -m scripts.eval --only prompt-injection --only contact-email

# Custom database (useful if you want results to persist)
python -m scripts.eval --db postgresql://user:pass@10.0.0.1/dobby_evals

# List available cases
python -m scripts.eval --help
```

Output:
1. **Console table:** summary verdicts (✓ pass, ✗ fail).
2. **JSON lines:** `evals/eval-<timestamp>.jsonl` — full transcript of each case (prompts, completions, tool calls, reasons).
3. **Markdown summary:** `evals/eval-<timestamp>.md` — readable report with failure reasons.
4. **Latest:** `evals/latest.md` — symlink to the most recent run.

## Interpreting results

A case **passes** if:
- The agent calls the expected tools.
- The agent produces the expected outcome (event created/updated/error).
- No safety violations (no credential leaks, respects allowlists).

A case **fails** if:
- The agent calls the wrong tools (e.g., tries to update when it should delete).
- The agent produces the wrong outcome (silently succeeds when it should error).
- The agent violates a safety boundary (leaks context, ignores restrictions).
- The agent crashes or times out.

### Example: contact lookup

**Case:** User requests a meeting and invites "Maya."

**Execution:**
1. Agent sees context includes discussions about Maya but no email.
2. Agent calls `ask_for_contact_email` (correct tool).
3. Model response: `"I need Maya's email"`
4. Agent waits for user response in the conversation.
5. User replies: `"maya@example.com"`.
6. Agent resumes, looks up the email in `contacts.json`, calls `calendar_create_event` with the invitee.
7. **Verdict:** PASS (correct sequence, safety observed).

**If it fails:**
- Agent doesn't ask (FAIL: incomplete request).
- Agent hallucinates an email (FAIL: uses made-up data).
- Agent creates event without invitee instead of asking (FAIL: silent failure).

## Adding a new evaluation case

Edit `scripts/eval_cases.py` and add a dict to the `CASES` list:

```python
{
    "name": "my-test-case",
    "description": "What this tests",
    "user_message": "@Dobby create a meeting tomorrow at 2pm",
    "context_messages": [
        {"author": "Alice", "text": "Let's discuss the launch"},
        {"author": "Bob", "text": "Good timing"},
    ],
    "expected_tools": [
        "calendar_create_event",
    ],
    "expected_outcome": "success",  # or "conflict", "error", "clarify"
    "notes": "Optional: explain why this matters",
}
```

Run the new case:
```bash
python -m scripts.eval --only my-test-case -v
```

## Stubbed tool results

Tool results are hardcoded in `STUB_RESULTS` in `eval_cases.py` so the same cases always produce the same outputs:

| Tool | Result |
| --- | --- |
| `calendar_search_events` | Returns 0–3 mock events (used to detect conflicts, find events by name) |
| `calendar_create_event` | Returns a mock event ID |
| `calendar_update_event` | Returns success |
| `calendar_delete_event` | Returns success |
| `ask_for_contact_email` | (Agent doesn't execute; tested in conversation history) |
| `composio_*` | All return `{"ok": true}` |

When a tool is called with arguments matching a pattern (e.g., `calendar_search_events` with query="release"), the stub returns the corresponding mock event list. This ensures evaluation results are reproducible.

## Failure modes to watch

### Common failures

1. **Hallucinated event IDs:** Agent creates an event without confirming the ID matches an existing one.
2. **Missing confirmation:** Agent skips the preview/confirm step.
3. **Ignored allowlists:** Agent ignores channel/role restrictions.
4. **Credential leaks:** Agent includes API keys, tokens, or email addresses in error messages.
5. **Prompt injection:** Malicious input in a message causes the agent to execute unintended tool calls.

### Debugging a failure

1. Check `evals/latest.md` for the failure reason.
2. Read the full transcript in `evals/eval-<timestamp>.jsonl`:
   ```bash
   grep "case_name" evals/eval-<timestamp>.jsonl | jq .
   ```
3. Compare the expected behavior (test definition) to what actually happened.
4. Trace through the agent code (`bot/agent.py`, `bot/tools.py`) to find the issue.
5. Add a unit test to `tests/test_agent.py` to catch the regression.

## Continuous evaluation

There is no CI automation for evaluations yet because they require Modal/Composio keys. Future options:

1. **Scheduled runs:** Post-merge evals detect regressions in production models.
2. **PR gates:** Block merge if key evals fail (requires secure key management).
3. **Baseline tracking:** Compare new runs against a known baseline.

## Evaluation infrastructure

- **Postgres database:** Evaluation runs store conversation history (agent messages, tool calls, completions) so later runs can compare agent behavior.
- **JSONL transcript:** Machine-readable complete transcript for parsing and comparison.
- **Markdown summary:** Human-readable report with pass/fail counts and failure reasons.

Database schema:
- `conversations`: one per evaluation case (ID, case name, user, timestamp).
- `messages`: agent messages, tool results (shared with production chat schema).

When debugging, you can query the database directly:
```bash
# Start a persistent Postgres for chat (includes evals)
make chat-db

# Connect with psql
psql postgresql://dobby:dobby@127.0.0.1:5433/dobby_test

# View latest evaluation
SELECT * FROM conversations ORDER BY created_at DESC LIMIT 1;
SELECT * FROM messages WHERE conversation_id = ? ORDER BY sequence;
```

## Relationship to unit tests

| Test Type | External Calls | Checks |
| --- | --- | --- |
| **Unit** | None (mocked) | Agent logic, tool shape, authorization |
| **Integration** | Postgres only | Database schema, migrations, ORM |
| **Evaluation** | Live model only | Agent behavior on real scenarios, end-to-end paths |

Evaluations catch issues unit tests miss (e.g., "the agent logic is correct, but the model ignores the instruction"). Unit tests catch issues evaluations miss (e.g., "the tool definition is malformed").

