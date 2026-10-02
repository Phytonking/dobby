"""Local chat REPL — talk to Dobby's agent pipeline without Discord.

Runs the exact code path a Discord mention takes (Agent.run: history load,
tool-call loop, tool execution, Postgres persistence), with stdin/stdout
instead of a Discord channel. Conversation history persists in the database
between runs, so you can quit, restart, and keep the context.

Modes:
    --fake   scripted Modal endpoint + Composio, no credentials, no network.
             Messages mentioning schedule/calendar/meeting/event demo the
             tool-call loop; everything else gets an echo reply.
    (live)   real Modal endpoint + Composio using MODAL_BASE_URL /
             MODAL_PROXY_TOKEN_ID / MODAL_PROXY_TOKEN_SECRET / MODAL_MODEL /
             COMPOSIO_API_KEY from the environment or .env. Tools really
             execute.

Usage:
    make chat-fake            # throwaway postgres + fake mode
    make chat                 # throwaway postgres + live keys from .env
    python -m scripts.chat --fake --db postgresql+asyncpg://...

Commands inside the REPL: /history, /clear, /quit.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "postgresql+asyncpg://dobby:dobby@127.0.0.1:5433/dobby_test"


# ---------------------------------------------------------------------------
# Fake Modal endpoint / Composio (mirrors tests/integration/test_pg_agent_e2e.py)
# ---------------------------------------------------------------------------

TOOL_WORDS = ("schedule", "calendar", "meeting", "event")


def _text_response(text):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call_response(tool_name, args):
    function = SimpleNamespace(name=tool_name, arguments=json.dumps(args))
    tool_call = SimpleNamespace(id="call_fake", function=function)
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAIClient:
    """Stateful script: tool-flavored messages get a tool call then a wrap-up;
    everything else gets an echo that proves history flowed in."""

    def __init__(self):
        completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=completions)
        self._pending_wrap_up = False

    def _create(self, *, model, messages, **kwargs):
        if self._pending_wrap_up:
            self._pending_wrap_up = False
            return _text_response("Done — event created (fake).")
        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user" and message.get("content"):
                last_user = message["content"]
                break
        if any(w in last_user.lower() for w in TOOL_WORDS):
            self._pending_wrap_up = True
            return _tool_call_response("GOOGLECALENDAR_CREATE_EVENT", {"summary": last_user[:60]})
        turns = sum(1 for m in messages if m.get("role") in ("user", "assistant"))
        return _text_response(f"(fake endpoint, turn {turns}) You said: {last_user}")


class FakeComposioSession:
    def execute(self, tool_name, arguments):
        print(f"  [tool] {tool_name}({arguments}) -> fake ok")
        return {"id": "evt_fake", "status": "confirmed"}

    def tools(self):
        return []


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def migrate(db_url):
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_URL"] = db_url  # read by migrations/env.py
    cfg = Config(str(REPO_ROOT / "migrations" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def build_agent(fake):
    from bot.agent import Agent

    if fake:
        from unittest.mock import patch

        fake_client = FakeOpenAIClient()
        composio = FakeComposioSession()
        with (
            patch("bot.agent.OpenAI", return_value=fake_client),
            patch("bot.agent.create_composio_session", return_value=(object(), composio)),
        ):
            agent = Agent(
                SimpleNamespace(
                    modal_base_url="https://fake.modal.direct/v1",
                    modal_token="fake-id.fake-secret",
                    composio_key="fake",
                    composio_entity_id="local-chat",
                    model="fake-model",
                    timezone=os.environ.get("TEAM_TIMEZONE", "America/Los_Angeles"),
                    reasoning_effort=None,
                    max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS", "40")),
                    max_completion_tokens=int(os.environ.get("MODEL_MAX_TOKENS", "8192")),
                )
            )
            agent._ensure_tools()  # bind fakes while the patches are active
        return agent

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    required = (
        "MODAL_BASE_URL",
        "MODAL_PROXY_TOKEN_ID",
        "MODAL_PROXY_TOKEN_SECRET",
        "MODAL_MODEL",
        "COMPOSIO_API_KEY",
    )
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"live mode needs {', '.join(missing)} (in env or .env) — or use --fake")
    base_url = os.environ["MODAL_BASE_URL"].rstrip("/").removesuffix("/chat/completions")
    token = f"{os.environ['MODAL_PROXY_TOKEN_ID']}.{os.environ['MODAL_PROXY_TOKEN_SECRET']}"
    return Agent(
        SimpleNamespace(
            modal_base_url=base_url,
            modal_token=token,
            composio_key=os.environ["COMPOSIO_API_KEY"],
            composio_entity_id=os.environ.get("COMPOSIO_ENTITY_ID", "dobby"),
            model=os.environ["MODAL_MODEL"],
            timezone=os.environ.get("TEAM_TIMEZONE", "America/Los_Angeles"),
            reasoning_effort=os.environ.get("MODAL_REASONING_EFFORT") or None,
            max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS", "40")),
            max_completion_tokens=int(os.environ.get("MODEL_MAX_TOKENS", "8192")),
        )
    )


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


async def repl(args):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bot.memory import load_history

    agent = build_agent(args.fake)
    engine = create_async_engine(args.db)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    mode = "FAKE (no keys, scripted)" if args.fake else f"LIVE ({agent.config.model})"
    print(f"dobby chat — {mode}")
    print(f"db={args.db} guild={args.guild} channel={args.channel}")
    print("commands: /history /clear /quit\n")

    try:
        while True:
            try:
                text = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text in ("/quit", "/q", "exit"):
                break
            if text == "/history":
                async with session_factory() as session:
                    rows = await load_history(session, args.guild, args.channel, limit=50)
                for r in rows:
                    label = r["tool_name"] if r["role"] == "tool" else (r["content"] or "")
                    print(f"  {r['role']:>5}: {label[:100]}")
                continue
            if text == "/clear":
                import sqlalchemy as sa

                async with session_factory() as session:
                    await session.execute(
                        sa.text("DELETE FROM conversation_history WHERE guild_id = :g AND channel_id = :c"),
                        {"g": args.guild, "c": args.channel},
                    )
                    await session.commit()
                print("  history cleared")
                continue

            # Same shape as on_message: one DB session per request.
            async with session_factory() as session:
                reply = await agent.run(
                    session=session,
                    request=text,
                    guild_id=args.guild,
                    channel_id=args.channel,
                    discord_user_id="local-user",
                    entity_id=agent.config.composio_entity_id,
                )
            print(f"dobby> {reply}\n")
    finally:
        await engine.dispose()
    print("bye")


def main():
    parser = argparse.ArgumentParser(description="Chat with Dobby's agent locally (no Discord).")
    parser.add_argument(
        "--fake", action="store_true", help="scripted Modal endpoint/Composio, no credentials"
    )
    parser.add_argument("--db", default=os.environ.get("TEST_DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--guild", default="local-guild")
    parser.add_argument("--channel", default="local-channel")
    args = parser.parse_args()
    # migrations/env.py calls asyncio.run itself — must happen outside the REPL loop
    migrate(args.db)
    asyncio.run(repl(args))


if __name__ == "__main__":
    main()
