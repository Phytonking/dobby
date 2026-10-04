"""Behavioral eval runner: real model, real production tool registry, stubbed execution.

Sends scripts/eval_cases.py through the production Agent.run path. The model comes from the same
AI_* settings the bot uses (env or .env). Tool schemas are fetched read-only from Composio
(COMPOSIO_API_KEY) into the bot's own registry, so the model sees exactly what production offers.
Composio execution is stubbed at bot.composio.execute_tool: no real calendar, Notion, Instagram or
LinkedIn action ever happens, and calendar writes stop at the confirmation proposal like they do
in Discord. Postgres holds users and the audit trail exactly like production.

Usage:
    make eval                 # throwaway postgres + full suite
    python -m scripts.eval --only prompt-injection --only team-time
    python -m scripts.eval --db postgresql+asyncpg://...

Output: verdict table on stdout, full transcripts in evals/eval-<ts>.jsonl
and a markdown summary in evals/eval-<ts>.md (latest copied to evals/latest.md).
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "postgresql+asyncpg://dobby:dobby@127.0.0.1:5433/dobby_test"
EVAL_DIR = REPO_ROOT / "evals"
REQUESTER = "900000000000000000"
# Known people for invite cases; Raj deliberately has no email so the missing-invitee path runs, and the
# two Sams share a first name (only one has an email) so the ambiguous-name path runs.
PEOPLE = (
    ("900000000000000001", "Maya Patel", "maya@example.edu"),
    ("900000000000000002", "Leonard Cho", "leonard@example.edu"),
    ("900000000000000003", "Raj Mehta", None),
    ("900000000000000004", "Sam Rivera", "sam.rivera@example.edu"),
    ("900000000000000005", "Sam Okafor", None),
)

sys.path.insert(0, str(REPO_ROOT))

# Must run before importing eval_cases: it reads MAX_TOOL_CALLS from os.environ at import time.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env", override=False)

from scripts.eval_cases import CASES, STUB_RESULTS  # noqa: E402


class StubComposio:
    """Stands in for bot.composio.execute_tool; every call is canned, nothing reaches Composio."""

    def __init__(self):
        self.fail = False

    def execute(self, toolset, tool_name, params, entity_id):
        if self.fail:
            return {"success": False, "error": "stubbed failure: Calendar API rate limited"}
        if tool_name == "GOOGLECALENDAR_EVENTS_GET":
            # Edits and deletes load the event first; give the proposal a real single meeting.
            event = {
                "id": params.get("event_id"),
                "summary": "Officer meeting",
                "start": {"dateTime": "2026-10-06T18:00:00-07:00"},
                "end": {"dateTime": "2026-10-06T19:00:00-07:00"},
            }
            return {"success": True, "data": event}
        for prefix, data in STUB_RESULTS.items():
            if tool_name.startswith(prefix):
                return {"success": True, "data": data}
        return {"success": True, "data": {}}


def recording_agent_class():
    from bot.agent import Agent

    class RecordingAgent(Agent):
        """Records every tool the model calls, including local ones that never reach Composio."""

        calls: list

        async def _call(self, ctx, name, params):
            record = {"tool": name, "args": params}
            self.calls.append(record)
            result = await super()._call(ctx, name, params)
            record["ok"] = bool(result.get("success"))
            if result.get("error"):
                record["error"] = str(result["error"])[:200]
            return result

    return RecordingAgent


def migrate(db_url):
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_URL"] = db_url
    cfg = Config(str(REPO_ROOT / "migrations" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def build_agent():
    from bot.AIModels import ModelSettings
    from bot.composio import get_toolset
    from bot.integrations import build_registry
    from bot.models import ConfigError

    try:
        settings = ModelSettings.from_env()
    except ConfigError as exc:
        sys.exit(f"eval needs the bot's AI_* settings (env or .env): {exc}")
    if not os.environ.get("COMPOSIO_API_KEY"):
        sys.exit("eval needs COMPOSIO_API_KEY to fetch the production tool schemas (nothing executes)")

    toolset = get_toolset(os.environ["COMPOSIO_API_KEY"])
    config = SimpleNamespace(
        ai_settings=settings,
        model=settings.primary.model,
        timezone=os.environ.get("TEAM_TIMEZONE", "America/Los_Angeles"),
        composio_entity="eval",
        max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS", "40")),
        instagram_user_id="eval-instagram-account",
        notion_parent_page_id="",
    )
    agent = recording_agent_class()(config, build_registry(toolset))
    agent.toolset = toolset
    agent.calls = []
    return agent


async def seed_people(session_factory):
    import sqlalchemy as sa

    async with session_factory() as session:
        for discord_id, name, email in PEOPLE:
            await session.execute(
                sa.text(
                    "INSERT INTO users (discord_id, display_name, calendar_email) VALUES (:d, :n, :e) "
                    "ON CONFLICT (discord_id) DO UPDATE SET display_name = :n, calendar_email = :e"
                ),
                {"d": discord_id, "n": name, "e": email},
            )
        await session.commit()


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def judge(case, tool_calls, final_reply, error, previews=()):
    """Return (verdict, reasons). FAIL = hard break, WARN = soft miss."""
    reasons = []
    if error:
        return "FAIL", [f"exception: {error}"]

    called = [c["tool"] for c in tool_calls]
    reply_lower = (final_reply or "").lower()

    for prefix in case.get("forbid_tools", []):
        hits = [t for t in called if t.startswith(prefix)]
        if hits:
            reasons.append(f"forbidden tool called: {hits}")
    for prefix in case.get("expect_tools") or []:
        if not any(t.startswith(prefix) for t in called):
            reasons.append(f"expected tool not called: {prefix}")
    if case.get("expect_tools") == [] and called:
        reasons.append(f"expected no tools, called: {called}")
    cap = case.get("max_tool_calls")
    if cap is not None and len(called) > cap:
        reasons.append(f"tool calls {len(called)} > cap {cap}")
    if case.get("tool_or_question") and not called and "?" not in (final_reply or ""):
        reasons.append("neither used a tool nor asked a clarifying question")
    if not (final_reply or "").strip():
        reasons.append("empty reply")
    if case.get("check"):
        try:
            reasons.extend(case["check"](tool_calls, list(previews)))
        except Exception as exc:  # a broken check must not read as a pass
            reasons.append(f"check raised {type(exc).__name__}: {exc}")
    if reasons:
        return "FAIL", reasons

    if case.get("reply_any"):
        if not any(s in reply_lower for s in case["reply_any"]):
            return "WARN", [f"reply missing all of {case['reply_any']}"]
    return "PASS", []


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_case(agent, stub, session_factory, case, run_tag):
    guild = f"eval-{run_tag}-{case['id']}"
    agent.calls = []
    stub.fail = bool(case.get("fail_tools"))
    transcript, context = [], []
    error = None
    started = time.monotonic()

    try:
        for turn in case["turns"]:
            async with session_factory() as session:
                result = await agent.run(session, turn, guild, "eval", REQUESTER, context=list(context))
            transcript.append(
                {"user": turn, "dobby": result.text, "pending": [p.preview for p in result.pending]}
            )
            # Discord context is recent human messages only; Dobby's own replies are not in it.
            context.append(f"[{datetime.now():%Y-%m-%d %H:%M}] Eval User: {turn}")
    except Exception as exc:  # keep the suite running; the case fails
        error = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started) * 1000)
    final_reply = transcript[-1]["dobby"] if transcript else ""
    previews = [preview for turn in transcript for preview in turn["pending"]]
    verdict, reasons = judge(case, agent.calls, final_reply, error, previews)
    return {
        "id": case["id"],
        "category": case["category"],
        "verdict": verdict,
        "reasons": reasons,
        "tool_calls": list(agent.calls),
        "transcript": transcript,
        "duration_ms": duration_ms,
    }


def write_reports(results, model):
    EVAL_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    jsonl_path = EVAL_DIR / f"eval-{ts}.jsonl"
    with jsonl_path.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("PASS", "WARN", "FAIL")}
    lines = [
        f"# Dobby eval — {ts}",
        f"model: {model}  |  PASS {counts['PASS']} / WARN {counts['WARN']} / FAIL {counts['FAIL']}",
        "",
        "| case | category | verdict | tools called | ms | notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        tools = ", ".join(c["tool"] for c in r["tool_calls"]) or "—"
        notes = "; ".join(r["reasons"]) or ""
        lines.append(
            f"| {r['id']} | {r['category']} | {r['verdict']} | {tools} | {r['duration_ms']} | {notes} |"
        )
    lines.append("")
    for r in results:
        lines.append(f"## {r['id']} — {r['verdict']}")
        for t in r["transcript"]:
            lines.append(f"- **user:** {t['user']}")
            lines.append(f"- **dobby:** {t['dobby']}")
            for preview in t["pending"]:
                lines.append(f"- **queued for confirmation:** {preview}")
        if r["tool_calls"]:
            lines.append(f"- tools: `{json.dumps(r['tool_calls'])[:500]}`")
        if r["reasons"]:
            lines.append(f"- notes: {'; '.join(r['reasons'])}")
        lines.append("")
    md = "\n".join(lines)
    md_path = EVAL_DIR / f"eval-{ts}.md"
    md_path.write_text(md)
    (EVAL_DIR / "latest.md").write_text(md)
    return jsonl_path, md_path


async def main_async(args):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    stub = StubComposio()
    agent = build_agent()
    engine = create_async_engine(args.db)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    run_tag = uuid.uuid4().hex[:8]

    cases = CASES
    if args.only:
        cases = [c for c in CASES if c["id"] in set(args.only)]
        missing = set(args.only) - {c["id"] for c in cases}
        if missing:
            sys.exit(f"unknown case ids: {sorted(missing)}")

    print(f"running {len(cases)} cases against {agent.config.model}\n")
    results = []
    try:
        await seed_people(session_factory)
        with patch("bot.composio.execute_tool", stub.execute):
            for case in cases:
                result = await run_case(agent, stub, session_factory, case, run_tag)
                results.append(result)
                mark = {"PASS": "✅", "WARN": "🟡", "FAIL": "❌"}[result["verdict"]]
                tools = ", ".join(c["tool"] for c in result["tool_calls"]) or "no tools"
                print(
                    f"{mark} {result['id']:24} [{result['category']:8}] {tools} ({result['duration_ms']}ms)"
                )
                for reason in result["reasons"]:
                    print(f"     ↳ {reason}")
    finally:
        await engine.dispose()

    jsonl_path, md_path = write_reports(results, agent.config.model)
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("PASS", "WARN", "FAIL")}
    print(f"\nPASS {counts['PASS']} / WARN {counts['WARN']} / FAIL {counts['FAIL']}")
    print(f"report: {md_path}\n        {jsonl_path}")
    return 1 if counts["FAIL"] else 0


def main():
    parser = argparse.ArgumentParser(description="Run behavioral evals against the live model.")
    parser.add_argument("--db", default=os.environ.get("TEST_DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--only", action="append", help="run only these case ids (repeatable)")
    args = parser.parse_args()
    migrate(args.db)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
