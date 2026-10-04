"""Local chat REPL — talk to Dobby's agent pipeline without Discord.

Runs the path a Discord mention takes (Agent.run with the production tool registry) on
stdin/stdout. As in Discord, the model sees your recent messages as channel context (Dobby's own
replies are not part of it) and nothing is stored as history. Calendar writes and social posts
come back as proposals; the REPL asks before executing them, like the 🟢 reaction in Discord.

Modes:
    --fake   scripted model + fake Composio, no credentials, no network. Messages mentioning
             schedule/calendar/meeting/event demo a proposal and its confirmation; anything else
             gets an echo.
    (live)   the bot's real model (AI_* settings) and Composio (COMPOSIO_API_KEY) from the
             environment or .env. Confirmed proposals really execute.

Usage:
    make chat-fake            # throwaway postgres + fake mode
    make chat                 # throwaway postgres + live keys from .env
    python -m scripts.chat --fake --db postgresql+asyncpg://...

Commands inside the REPL: /context, /clear, /quit.
"""

import argparse
import asyncio
import json
import os
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "postgresql+asyncpg://dobby:dobby@127.0.0.1:5433/dobby_test"
REQUESTER = "900000000000000000"
TOOL_WORDS = ("schedule", "calendar", "meeting", "event")


# ---------------------------------------------------------------------------
# Fake model / Composio (mirrors tests/integration/test_pg_agent_e2e.py)
# ---------------------------------------------------------------------------


def _reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _tool_call(name, args):
    call = {"id": "call_fake", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]}


class FakeModel:
    """Scripted OpenAI-format endpoint: tool-flavored requests propose an event, the rest echo."""

    def __init__(self):
        self._wrap_up = False

    def handle(self, request):
        import httpx

        body = json.loads(request.content)
        if self._wrap_up:
            self._wrap_up = False
            return httpx.Response(200, json=_reply("Dobby has prepared it — please confirm (fake)."))
        last_user = next(
            (m["content"] for m in reversed(body["messages"]) if m["role"] == "user" and m.get("content")), ""
        )
        if any(w in last_user.lower() for w in TOOL_WORDS):
            self._wrap_up = True
            start = (datetime.now() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
            args = {
                "summary": last_user[:60],
                "start_datetime": start.isoformat(timespec="seconds"),
                "deferred_invitees": [],
            }
            return httpx.Response(200, json=_tool_call("GOOGLECALENDAR_CREATE_EVENT", args))
        return httpx.Response(200, json=_reply(f"(fake model) You said: {last_user}"))


class FakeComposioTools:
    def get_raw_composio_tools(self, tools):
        return [
            SimpleNamespace(slug=t, description=t, input_parameters={"type": "object", "properties": {}})
            for t in tools
        ]


def fake_execute(toolset, tool_name, params, entity_id):
    print(f"  [composio] {tool_name}({params}) -> fake ok")
    return {"success": True, "data": {"id": "evt_fake", "status": "confirmed"}}


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


def build_agent(fake, stack):
    from bot.AIModels import Endpoint, ModelSettings
    from bot.agent import Agent
    from bot.composio import get_toolset
    from bot.integrations import build_registry
    from bot.models import ConfigError

    if fake:
        import httpx

        model = FakeModel()
        client_class = httpx.AsyncClient
        stack.enter_context(
            patch(
                "bot.AIModels.httpx.AsyncClient",
                lambda **kw: client_class(transport=httpx.MockTransport(model.handle), **kw),
            )
        )
        stack.enter_context(patch("bot.composio.execute_tool", fake_execute))
        settings = ModelSettings(Endpoint("openai-compatible", "fake-model", "fake", "https://fake.model/v1"))
        toolset, entity = SimpleNamespace(tools=FakeComposioTools()), "local-chat"
    else:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env", override=False)
        try:
            settings = ModelSettings.from_env()
        except ConfigError as exc:
            sys.exit(f"live mode needs the bot's AI_* settings (env or .env): {exc} — or use --fake")
        if not os.environ.get("COMPOSIO_API_KEY"):
            sys.exit("live mode needs COMPOSIO_API_KEY (env or .env) — or use --fake")
        toolset = get_toolset(os.environ["COMPOSIO_API_KEY"])
        entity = os.environ.get("COMPOSIO_ENTITY_ID", "dobby").strip() or "dobby"

    config = SimpleNamespace(
        ai_settings=settings,
        model=settings.primary.model,
        timezone=os.environ.get("TEAM_TIMEZONE", "America/Los_Angeles"),
        composio_entity=entity,
        max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS", "40")),
        context_limit=int(os.environ.get("CONTEXT_MESSAGE_LIMIT", "50")),
        instagram_user_id=os.environ.get("INSTAGRAM_USER_ID", ""),
        notion_parent_page_id=os.environ.get("NOTION_PARENT_PAGE_ID", ""),
    )
    agent = Agent(config, build_registry(toolset))
    agent.toolset = toolset
    return agent


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


async def confirm_pending(pending):
    for action in pending:
        print(f"  [{action.label}]")
        for line in action.preview.splitlines():
            print(f"    {line}")
        answer = (await asyncio.to_thread(input, "  confirm? [y/N] ")).strip().lower()
        if answer not in ("y", "yes"):
            print("  cancelled — nothing changed")
            continue
        outcome = await action.execute()
        if outcome.get("success"):
            print(f"  {outcome.get('message') or 'done'}")
        else:
            print(f"  failed: {outcome.get('error')}")


async def repl(args):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    with ExitStack() as stack:
        agent = build_agent(args.fake, stack)
        engine = create_async_engine(args.db)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        context = []

        mode = "FAKE (no keys, scripted)" if args.fake else f"LIVE ({agent.config.model})"
        print(f"dobby chat — {mode}")
        print(f"db={args.db} guild={args.guild} channel={args.channel}")
        print("commands: /context /clear /quit\n")

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
                if text == "/context":
                    for line in context or ["  (empty)"]:
                        print(f"  {line}")
                    continue
                if text == "/clear":
                    context.clear()
                    print("  context cleared")
                    continue

                # Same shape as a Discord mention: one DB session per request.
                async with session_factory() as session:
                    result = await agent.run(
                        session,
                        text,
                        args.guild,
                        args.channel,
                        REQUESTER,
                        context=context[-agent.config.context_limit :],
                    )
                print(f"dobby> {result.text}\n")
                await confirm_pending(result.pending)
                context.append(f"[{datetime.now():%Y-%m-%d %H:%M}] You: {text}")
        finally:
            await engine.dispose()
    print("bye")


def main():
    parser = argparse.ArgumentParser(description="Chat with Dobby's agent locally (no Discord).")
    parser.add_argument("--fake", action="store_true", help="scripted model/Composio, no credentials")
    parser.add_argument("--db", default=os.environ.get("TEST_DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--guild", default="local-guild")
    parser.add_argument("--channel", default="local-channel")
    args = parser.parse_args()
    # migrations/env.py calls asyncio.run itself — must happen outside the REPL loop
    migrate(args.db)
    asyncio.run(repl(args))


if __name__ == "__main__":
    main()
