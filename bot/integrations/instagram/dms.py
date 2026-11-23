"""Instagram DMs via Composio: list conversations and messages, and send a reply behind Confirm.

Needs the `instagram_manage_messages` permission on the connected Meta app. Responses are parsed
defensively: Composio may pass the Graph API body through (`{"data": [...], "paging": ...}`) or
flatten it, so we look for the list we need rather than a fixed path. Nothing read here is stored;
message text goes straight to a Discord thread and is dropped.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from ...composio import find_key, run_action
from ...models import UserError
from ..base import PendingAction
from .publish import account_id

# Composio action names, verified against the instagram toolkit at the TOOLKIT_VERSIONS pin.
LIST_CONVERSATIONS = "INSTAGRAM_LIST_ALL_CONVERSATIONS"
LIST_MESSAGES = "INSTAGRAM_LIST_ALL_MESSAGES"
SEND_TEXT = "INSTAGRAM_SEND_TEXT_MESSAGE"
DM_ACTIONS = (LIST_CONVERSATIONS, LIST_MESSAGES, SEND_TEXT)

CONVERSATION_FIELDS = "id,updated_time"
MESSAGE_FIELDS = "id,created_time,from,to,message"
CONVERSATION_LIMIT = 20
MESSAGE_LIMIT = 10
MAX_DM_TEXT = 1000  # Instagram's limit for one text message

# IDs of messages Dobby sent, so the relay never echoes our own replies back into Discord even if
# Instagram reports our sender ID differently from INSTAGRAM_USER_ID. IDs only, process-local.
RECENTLY_SENT: deque[str] = deque(maxlen=200)


@dataclass(frozen=True)
class Conversation:
    id: str
    updated: datetime | None = None


@dataclass(frozen=True)
class Message:
    id: str
    created: datetime | None
    sender_id: str
    sender_name: str
    text: str


def parse_time(value) -> datetime | None:
    """Graph API times look like 2024-05-01T10:00:00+0000; also accept ISO with Z, or epoch seconds."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _items(data, *keys) -> list:
    """The first list found under any of `keys` (or `data` itself, if it is a list)."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            found = _items(value, *keys)
            if found:
                return found
    for value in data.values():
        if isinstance(value, dict):
            found = _items(value, *keys)
            if found:
                return found
    return []


def parse_conversations(data) -> list[Conversation]:
    conversations = []
    for item in _items(data, "data", "conversations", "items"):
        if isinstance(item, dict) and item.get("id"):
            conversations.append(Conversation(str(item["id"]), parse_time(item.get("updated_time"))))
    return conversations


def parse_messages(data) -> list[Message]:
    """Messages newest first, which is how the Graph API lists them."""
    messages = []
    for item in _items(data, "data", "messages", "items"):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        sender = item.get("from") if isinstance(item.get("from"), dict) else {}
        messages.append(
            Message(
                id=str(item["id"]),
                created=parse_time(item.get("created_time") or item.get("timestamp")),
                sender_id=str(sender.get("id") or ""),
                sender_name=str(sender.get("username") or sender.get("name") or ""),
                text=str(item.get("message") or item.get("text") or ""),
            )
        )
    if messages and all(m.created for m in messages):
        messages.sort(key=lambda m: m.created, reverse=True)
    return messages


async def list_conversations(toolset, config, entity_id: str) -> list[Conversation]:
    result = await run_action(
        toolset,
        LIST_CONVERSATIONS,
        {
            "platform": "instagram",
            "ig_user_id": account_id(config),
            "fields": CONVERSATION_FIELDS,
            "limit": CONVERSATION_LIMIT,
        },
        entity_id,
    )
    if not result.get("success"):
        raise UserError(f"Instagram did not answer: {result.get('error', 'unknown error')}")
    return parse_conversations(result.get("data"))


async def list_messages(toolset, conversation_id: str, entity_id: str) -> list[Message]:
    result = await run_action(
        toolset,
        LIST_MESSAGES,
        {"conversation_id": conversation_id, "fields": MESSAGE_FIELDS, "limit": MESSAGE_LIMIT},
        entity_id,
    )
    if not result.get("success"):
        raise UserError(f"Instagram did not answer: {result.get('error', 'unknown error')}")
    return parse_messages(result.get("data"))


def draft_reply(toolset, config, entity_id: str, recipient_id: str, text: str, *, after_success=None):
    """A PendingAction that sends exactly `text` to `recipient_id` once the requester confirms."""
    account_id(config)
    if not recipient_id:
        raise UserError("Dobby does not know who to answer in this conversation yet.")
    if not text or not text.strip():
        raise UserError("Tell Dobby what to send back.")
    if len(text) > MAX_DM_TEXT:
        raise UserError(f"Instagram messages are limited to {MAX_DM_TEXT} characters.")

    async def execute() -> dict:
        result = await run_action(
            toolset,
            SEND_TEXT,
            {"ig_user_id": account_id(config), "recipient_id": recipient_id, "text": text},
            entity_id,
        )
        sent = find_key(result.get("data"), "message_id", "id") if result.get("success") else None
        if sent:
            RECENTLY_SENT.append(str(sent))
        return result

    return PendingAction(
        integration="instagram_dm",
        label="Instagram DM reply",
        preview=text,  # exactly what will be sent
        execute=execute,
        after_success=after_success,
    )
