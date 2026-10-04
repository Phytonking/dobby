# Behavioral evals

Unit tests prove the code does what it's told; evals check what the live model chooses to do.
`scripts/eval.py` sends each case in `scripts/eval_cases.py` through the production
`Agent.run` path and judges the tool calls and the final reply.

## What's real and what's stubbed

- **Real:** the model (the bot's own `AI_*` settings, so Modal, Gemini, Claude, … whatever the
  bot runs), the tool registry (`build_registry` over schemas fetched read-only from Composio, so
  the model sees exactly what production offers), local tools such as `lookup_calendar_email`,
  the calendar proposal handler, and Postgres (seeded people, the audit trail).
- **Stubbed:** Composio execution, at `bot.composio.execute_tool`. Results come from
  `STUB_RESULTS`; nothing reaches a real calendar, Notion, Instagram or LinkedIn account. Calendar
  writes stop at the confirmation proposal, exactly as they do in Discord.
- **Context:** later turns see earlier ones the way Discord delivers them — as recent channel
  messages, human messages only. Nothing is stored as conversation history.

## Running

```bash
make eval                                            # throwaway Postgres + every case
python -m scripts.eval --only prompt-injection --only team-time
```

Needs the bot's `AI_*` settings and `COMPOSIO_API_KEY` in the environment or `.env`. Each run
writes `evals/eval-<timestamp>.jsonl` (full transcripts and tool arguments) and a Markdown
report, copied to `evals/latest.md`, which also lists every proposal that was queued.

## Cases

Fields, all optional except `id`, `category` and `turns`:

| Field | Meaning |
|---|---|
| `turns` | user messages sent in order in one channel |
| `expect_tools` | tool-name prefixes that must be called (`[]` = no tools at all) |
| `forbid_tools` | tool-name prefixes that must not be called |
| `reply_any` | the final reply must contain at least one of these (lowercase) |
| `tool_or_question` | pass if a tool ran or the reply asks a question |
| `fail_tools` | every stubbed Composio execution returns an error |
| `max_tool_calls` | ceiling on tool calls across the case |
| `check` | callable `(tool_calls, previews) -> [failure reasons]` for assertions on arguments, e.g. who is in `attendees` and which Discord IDs are deferred; each call is `{tool, args, ok, error}` |

Verdicts: **FAIL** for a hard break (forbidden or missing tool, cap exceeded, empty reply,
exception), **WARN** when only `reply_any` missed — read the transcript; often the model was
right and phrased it differently. The run exits non-zero on any FAIL.

To add a case, append it to `CASES`. If it relies on a Composio result, add the canned data to
`STUB_RESULTS` (keyed by tool-name prefix). People the model can look up are seeded in
`scripts/eval.py` (`PEOPLE`); Raj intentionally has no email so the missing-invitee path runs, and the two Sams share a first name (only one has an email) so the ambiguous-name path runs.

## Reading results

Model behavior isn't deterministic even at a fixed temperature: the same request can produce a
clarifying question on one run and a capped batch of tool calls on the next. Re-run a case before
treating a single FAIL as a regression, and compare transcripts rather than verdict counts.
