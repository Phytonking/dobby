"""Behavioral eval runner: real Modal-hosted model decisions, stubbed tool execution.

Sends scripts/eval_cases.py through the production Agent.run path. The model
is live (needs MODAL_BASE_URL / MODAL_PROXY_TOKEN_ID / MODAL_PROXY_TOKEN_SECRET
/ MODAL_MODEL in env or .env); Composio is replaced by a stub session so no
real calendar/github/notion action ever happens. Postgres holds conversation
history exactly like production.

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

sys.path.insert(0, str(REPO_ROOT))

# Must run before importing eval_cases: it reads MAX_TOOL_CALLS from
# os.environ at module load time, so .env has to be loaded first or a value
# set only in .env (not the shell) would be silently missed.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env", override=False)

from scripts.eval_cases import CASES, STUB_RESULTS, STUB_TOOLS  # noqa: E402


class StubComposioSession:
    """Real declarations shown to the model; execution returns canned data."""

    def __init__(self):
        self.calls = []
        self.fail_mode = False

    def tools(self):
        # Composio(api_key=...) defaults to OpenAIProvider, so real session.tools()
        # nests each declaration under "function" — this is the same shape
        # get_openai_tools passes straight through to `tools=` in production.
        return [{"type": "function", "function": t} for t in STUB_TOOLS]

    def execute(self, tool_name, arguments):
        self.calls.append({"tool": tool_name, "args": arguments})
        if self.fail_mode:
            raise RuntimeError("stubbed failure: Calendar API rate limited")
        for prefix, result in STUB_RESULTS.items():
            if tool_name.startswith(prefix):
                return result
        return {"ok": True}


def migrate(db_url):
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_URL"] = db_url
    cfg = Config(str(REPO_ROOT / "migrations" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def build_agent(stub):
    # .env already loaded at module import time (see top of file).
    required = ("MODAL_BASE_URL", "MODAL_PROXY_TOKEN_ID", "MODAL_PROXY_TOKEN_SECRET", "MODAL_MODEL")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"eval needs {', '.join(missing)} (env or .env)")

    from bot.agent import Agent

    base_url = os.environ["MODAL_BASE_URL"].rstrip("/").removesuffix("/chat/completions")
    token = f"{os.environ['MODAL_PROXY_TOKEN_ID']}.{os.environ['MODAL_PROXY_TOKEN_SECRET']}"
    with patch("bot.agent.create_composio_session", return_value=(object(), stub)):
        agent = Agent(
            SimpleNamespace(
                modal_base_url=base_url,
                modal_token=token,
                composio_key="stub",
                composio_entity_id="eval",
                model=os.environ["MODAL_MODEL"],
                timezone=os.environ.get("TEAM_TIMEZONE", "America/Los_Angeles"),
                reasoning_effort=os.environ.get("MODAL_REASONING_EFFORT") or None,
                max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS", "40")),
                max_completion_tokens=int(os.environ.get("MODEL_MAX_TOKENS", "8192")),
            )
        )
        agent._ensure_tools()  # bind the stub while create_composio_session is patched
    return agent


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def judge(case, tool_calls, final_reply, error):
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
    stub.calls = []
    stub.fail_mode = bool(case.get("fail_tools"))
    transcript = []
    error = None
    started = time.monotonic()

    try:
        for turn in case["turns"]:
            async with session_factory() as session:
                reply = await agent.run(
                    session=session,
                    request=turn,
                    guild_id=guild,
                    channel_id="eval",
                    discord_user_id="eval-user",
                    entity_id="eval",
                )
            transcript.append({"user": turn, "dobby": reply})
    except Exception as exc:  # keep the suite running; the case fails
        error = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started) * 1000)
    final_reply = transcript[-1]["dobby"] if transcript else ""
    verdict, reasons = judge(case, stub.calls, final_reply, error)
    return {
        "id": case["id"],
        "category": case["category"],
        "verdict": verdict,
        "reasons": reasons,
        "tool_calls": list(stub.calls),
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

    stub = StubComposioSession()
    agent = build_agent(stub)
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
        for case in cases:
            result = await run_case(agent, stub, session_factory, case, run_tag)
            results.append(result)
            mark = {"PASS": "✅", "WARN": "🟡", "FAIL": "❌"}[result["verdict"]]
            tools = ", ".join(c["tool"] for c in result["tool_calls"]) or "no tools"
            print(f"{mark} {result['id']:24} [{result['category']:8}] {tools} ({result['duration_ms']}ms)")
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
    parser = argparse.ArgumentParser(description="Run behavioral evals against the live Modal-hosted model.")
    parser.add_argument("--db", default=os.environ.get("TEST_DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--only", action="append", help="run only these case ids (repeatable)")
    args = parser.parse_args()
    migrate(args.db)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
