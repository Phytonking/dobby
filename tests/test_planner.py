from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from google import genai
from pydantic import ValidationError

from bot.models import Plan, UserError
from bot.planner import Planner
from bot.service import Scheduler


def test_standard_json_schema_and_local_validation():
    with patch("bot.planner.genai.Client") as client:
        generate = client.return_value.models.generate_content
        generate.return_value.text = '{"action":"clarify","question":"What time?"}'
        planner = Planner(SimpleNamespace(gemini_key="fake", model="test", timezone="UTC"))
        history = [{"author": "Ann", "text": "launch review?", "at": "2030-01-01T00:00:00Z"}]
        place = {"channel": "planning", "thread": "Q4 launch"}
        assert planner.plan("Schedule a meeting", None, history, place).question == "What time?"
        sent = json.loads(generate.call_args.kwargs["contents"])
        assert sent["recent_messages"] == history
        assert sent["place"] == place
        config = generate.call_args.kwargs["config"]
        assert config.response_schema is None
        assert config.response_json_schema == Plan.model_json_schema()
        assert config.response_json_schema["additionalProperties"] is False
        assert config.response_mime_type == "application/json"

        generate.return_value.text = '{"action":"clarify","unexpected":true}'
        with pytest.raises(ValidationError):
            planner.plan("Schedule a meeting")


@contextmanager
def transport_planner(statuses):
    """Exercise the real SDK serializer and retry loop without network or credentials."""
    requests = []
    responses = iter(statuses)
    real_client = genai.Client

    def respond(request):
        requests.append(json.loads(request.content))
        code = next(responses)
        if code != 200:
            return httpx.Response(code, json={"error": {"code": code, "message": "private request data"}})
        plan = {"action": "create", "summary": "Sync", "start": "2099-01-01T10:00:00Z"}
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": json.dumps(plan)}]}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:

        def make_client(**kwargs):
            kwargs["http_options"].httpx_client = http_client
            return real_client(**kwargs)

        with (
            patch("bot.planner.genai.Client", side_effect=make_client),
            patch("tenacity.nap.time.sleep") as sleep,
        ):
            planner = Planner(SimpleNamespace(gemini_key="fake", model="test", timezone="UTC"))
            try:
                yield planner, requests, sleep
            finally:
                planner.client.close()


def test_overload_retries_real_sdk_json_schema_then_prepares_calendar_body():
    with transport_planner([503, 503, 200]) as (planner, requests, sleep):
        calendar = Mock()
        proposal = Scheduler(planner, calendar, "UTC").prepare("Schedule Sync", None, 123)
        assert proposal.action == "create"
        assert proposal.body["summary"] == "Sync"
        assert proposal.body["end"]["dateTime"] == "2099-01-01T11:00:00+00:00"
        calendar.check_conflicts.assert_called_once_with(proposal.body, None)
        calendar.apply.assert_not_called()
        assert len(requests) == 3
        assert requests[0] == requests[1] == requests[2]
        wire_config = requests[0]["generationConfig"]
        assert "responseSchema" not in wire_config
        assert wire_config["responseJsonSchema"]["additionalProperties"] is False
        assert wire_config["responseMimeType"] == "application/json"
        assert sleep.call_count == 2
        assert 1 <= sleep.call_args_list[0].args[0] <= 2
        assert 2 <= sleep.call_args_list[1].args[0] <= 3


@pytest.mark.parametrize(
    ("code", "attempts", "message"),
    [
        (503, 3, "temporarily unavailable"),
        (429, 3, "rate limit or quota"),
        (400, 1, "structured-output code"),
    ],
)
def test_provider_failure_is_bounded_and_never_writes(code, attempts, message):
    with transport_planner([code] * attempts) as (planner, requests, sleep):
        calendar = Mock()
        with pytest.raises(UserError, match=message) as caught:
            Scheduler(planner, calendar, "UTC").prepare("Schedule Sync", None, 123)
        assert "private request data" not in str(caught.value)
        assert "No calendar changes were made" in str(caught.value)
        assert len(requests) == attempts
        assert sleep.call_count == attempts - 1
        calendar.check_conflicts.assert_not_called()
        calendar.apply.assert_not_called()
