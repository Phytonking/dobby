import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from google import genai
from google.genai import types

from bot.chat import Concierge, Reply, classify, describe
from bot.voice import load_lines


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("what can you do?", "capabilities"),
        ("Dobby, what are your commands", "capabilities"),
        ("help", "capabilities"),
        ("schedule a sync tomorrow at 3pm", "schedule"),
        ("delete the design review", "schedule"),
        ("maya@example.com", "unsure"),
        ("how are you feeling?", "unsure"),
        ("help me set up a meeting", "unsure"),
    ],
)
def test_classify_fast_paths(text, kind):
    assert classify(text) == kind


def test_describe_lists_examples_and_timezone():
    text = describe("America/Denver")
    assert "@Dobby schedule" in text and "/contacts" in text and "/events" in text
    assert "🟢" in text and "America/Denver" in text
    assert text.startswith(load_lines("capabilities_intro")[0])


def config():
    return SimpleNamespace(timezone="UTC", model="test", gemini_key="fake")


def test_scheduling_and_capability_messages_never_call_gemini():
    client = Mock()
    concierge = Concierge(config(), client)
    assert concierge.respond("book a meeting friday at 2pm") == ("schedule", None)
    kind, text = concierge.respond("what can you do")
    assert kind == "capabilities" and "/events" in text
    client.models.generate_content.assert_not_called()


def test_unsure_messages_are_decided_by_gemini_and_chat_gets_a_farewell():
    client = Mock()
    generate = client.models.generate_content
    concierge = Concierge(config(), client)

    generate.return_value.text = json.dumps({"kind": "schedule"})
    assert concierge.respond("that works for me") == ("schedule", None)
    sent = json.loads(generate.call_args.kwargs["contents"])
    assert sent["message"] == "that works for me"
    cfg = generate.call_args.kwargs["config"]
    assert cfg.response_json_schema == Reply.model_json_schema()
    assert cfg.response_mime_type == "application/json"

    generate.return_value.text = json.dumps({"kind": "chat", "reply": "Dobby thinks tea is best."})
    kind, text = concierge.respond("what is your favourite drink?")
    assert kind == "chat"
    assert text.startswith("Dobby thinks tea is best.\n\n")
    assert text.endswith(load_lines("farewell")[0])

    generate.return_value.text = json.dumps({"kind": "capabilities"})
    kind, text = concierge.respond("so, um, what is this thing")
    assert kind == "capabilities" and "🟢" in text


def test_gemini_failure_or_bad_schema_falls_back_in_voice_without_leaking():
    def respond(request):
        return httpx.Response(500, json={"error": {"code": 500, "message": "private request data"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = genai.Client(api_key="fake", http_options=types.HttpOptions(httpx_client=http_client))
        try:
            kind, text = Concierge(config(), client).respond("how are you?")
        finally:
            client.close()
    assert kind == "chat"
    assert "private request data" not in text
    assert text.startswith(load_lines("chat_fallback")[0])
    assert text.endswith(load_lines("farewell")[0])

    client = Mock()
    client.models.generate_content.return_value.text = '{"kind": "chat", "unexpected": 1}'
    kind, text = Concierge(config(), client).respond("how are you?")
    assert kind == "chat" and text.endswith(load_lines("farewell")[0])


def test_every_farewell_says_dobby_is_leaving():
    for line in load_lines("farewell"):
        assert "Dobby" in line
        assert any(word in line.lower() for word in ("going", "go", "leav", "off", "depart", "vanish"))
