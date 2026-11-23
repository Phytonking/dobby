"""Unit tests for Instagram DM parsing, the cursor walk and the confirm hook (behaviour is in the BDD suite)."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from bot.integrations.base import PendingAction
from bot.integrations.discord.confirm import ConfirmView
from bot.integrations.instagram import dms
from bot.integrations.instagram.relay import format_dm, new_messages, relay_parent_id
from bot.models import UserError

CONFIG = SimpleNamespace(instagram_user_id="1784", composio_entity="dobby")
T = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-11-22T10:00:00+0000", T),
        ("2026-11-22T10:00:00Z", T),
        ("2026-11-22T10:00:00", T),
        (T.timestamp(), T),
        ("yesterday", None),
        (None, None),
    ],
)
def test_parse_time_accepts_graph_api_shapes(value, expected):
    assert dms.parse_time(value) == expected


def test_parsers_accept_wrapped_and_flattened_responses():
    item = {"id": "m1", "created_time": "2026-11-22T10:00:00+0000", "from": {"id": "9", "username": "u"}}
    wrapped = {"data": [item], "paging": {}}
    flattened = {"messages": [item]}
    nested = {"response_data": {"messages": {"data": [item]}}}
    for shape in (wrapped, flattened, nested, [item]):
        (message,) = dms.parse_messages(shape)
        assert (message.id, message.sender_id, message.sender_name, message.text) == ("m1", "9", "u", "")
    assert dms.parse_messages({"data": "nope"}) == [] and dms.parse_messages(None) == []
    (conversation,) = dms.parse_conversations({"data": {"data": [{"id": "c1", "updated_time": None}]}})
    assert conversation == dms.Conversation("c1", None)


def test_new_messages_stops_at_the_cursor_or_its_time():
    def m(mid, minute):
        created = T.replace(minute=minute) if minute is not None else None
        return dms.Message(mid, created, "9", "u", mid)

    newest_first = [m("m4", 4), m("m3", 3), m("m2", 2), m("m1", 1)]
    assert [x.id for x in new_messages(newest_first, "m2", T.replace(minute=2))] == ["m3", "m4"]
    # Cursor ID gone (deleted message): the timestamp still stops the walk. A different message at
    # exactly the cursor's time is kept, so two DMs sent in the same second are not lost.
    assert [x.id for x in new_messages(newest_first, "gone", T.replace(minute=2))] == ["m2", "m3", "m4"]
    assert [x.id for x in new_messages(newest_first, "gone", T.replace(minute=2, second=30))] == ["m3", "m4"]
    # Never-seen conversation: only what is newer than the bot's start.
    assert [x.id for x in new_messages(newest_first, None, T.replace(minute=3))] == ["m4"]
    assert new_messages([m("x", None)], None, T) == []


def test_format_dm_quotes_text_and_explains_attachments():
    text = format_dm(dms.Message("m", T, "9", "maya", "line one\nline two"))
    assert text.startswith("**@maya** on Instagram") and "> line one\n> line two" in text
    assert "attachment" in format_dm(dms.Message("m", None, "9", "", ""))


def test_relay_parent_id_only_for_threads_of_the_relay_channel():
    config = SimpleNamespace(instagram_dm_channel=900)
    assert relay_parent_id(config, SimpleNamespace(parent_id=900)) == 900
    assert relay_parent_id(config, SimpleNamespace(parent_id=901)) is None
    assert relay_parent_id(config, SimpleNamespace(id=900)) is None
    assert relay_parent_id(SimpleNamespace(instagram_dm_channel=0), SimpleNamespace(parent_id=0)) is None


def test_draft_reply_validates_and_sends_exact_text_only_on_execute():
    with pytest.raises(UserError):
        dms.draft_reply("ts", CONFIG, "dobby", "9", "   ")
    with pytest.raises(UserError):
        dms.draft_reply("ts", CONFIG, "dobby", "", "hi")
    with pytest.raises(UserError):
        dms.draft_reply("ts", CONFIG, "dobby", "9", "x" * (dms.MAX_DM_TEXT + 1))

    async def run():
        reply = {"success": True, "data": {"message_id": "mid.out"}}
        with patch.object(dms, "run_action", new=AsyncMock(return_value=reply)) as call:
            pending = dms.draft_reply("ts", CONFIG, "dobby", "9", " hi *there* ")
            call.assert_not_awaited()
            assert pending.preview == " hi *there* "
            await pending.execute()
        assert call.await_args.args[1:] == (
            dms.SEND_TEXT,
            {"ig_user_id": "1784", "recipient_id": "9", "text": " hi *there* "},
            "dobby",
        )
        assert "mid.out" in dms.RECENTLY_SENT

    asyncio.run(run())


def test_confirm_view_runs_after_success_only_for_actions_that_succeeded():
    async def run():
        hook_ok, hook_failed = AsyncMock(), AsyncMock()
        ok = PendingAction("x", "A", "a", AsyncMock(return_value={"success": True}), after_success=hook_ok)
        bad = PendingAction(
            "x", "B", "b", AsyncMock(return_value={"success": False}), after_success=hook_failed
        )
        view = ConfirmView([ok, bad], requester_id=1, guild_id="10", channel_id="40")
        click = Mock()
        click.user.id = 1
        click.response.edit_message = AsyncMock()
        click.edit_original_response = AsyncMock()
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=AsyncMock(commit=AsyncMock()))
        session.__aexit__ = AsyncMock(return_value=None)
        with (
            patch("bot.integrations.discord.confirm.SessionLocal", return_value=session),
            patch("bot.integrations.discord.confirm.record_action", new=AsyncMock()),
        ):
            await view.confirm.callback(click)
        hook_ok.assert_awaited_once_with(click.channel)
        hook_failed.assert_not_awaited()

    asyncio.run(run())
