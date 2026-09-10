from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from bot.models import Plan
from bot.planner import Planner


def test_standard_json_schema_and_local_validation():
    with patch("bot.planner.genai.Client") as client:
        generate = client.return_value.models.generate_content
        generate.return_value.text = '{"action":"clarify","question":"What time?"}'
        planner = Planner(SimpleNamespace(gemini_key="fake", model="test", timezone="UTC"))
        assert planner.plan("Schedule a meeting").question == "What time?"
        config = generate.call_args.kwargs["config"]
        assert config.response_schema is None
        assert config.response_json_schema == Plan.model_json_schema()
        assert config.response_json_schema["additionalProperties"] is False
        assert config.response_mime_type == "application/json"

        generate.return_value.text = '{"action":"clarify","unexpected":true}'
        with pytest.raises(ValidationError):
            planner.plan("Schedule a meeting")
