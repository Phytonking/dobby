import os
from unittest.mock import patch

import pytest

from bot.config import Config


@pytest.mark.parametrize(
    "value,expected", [(None, "gemini-3.1-flash-lite"), (" custom ", "custom"), ("", "")]
)
def test_backup_model_configuration(value, expected):
    env = {
        "DOBBY_ENV_FILE": "/nonexistent.env",
        "DISCORD_TOKEN": "test",
        "DISCORD_GUILD_ID": "1",
        "ALLOWED_USER_IDS": "1",
        "GEMINI_API_KEY": "test",
        "COMPOSIO_API_KEY": "test",
        "DATABASE_URL": "test",
    }
    if value is not None:
        env["GEMINI_MODEL_BACKUP"] = value
    with patch.dict(os.environ, env, clear=True):
        assert Config.load().model_backup == expected


BASE_ENV = {
    "DOBBY_ENV_FILE": "/nonexistent.env",
    "DISCORD_TOKEN": "test",
    "DISCORD_GUILD_ID": "1",
    "ALLOWED_USER_IDS": "1",
    "GEMINI_API_KEY": "test",
    "COMPOSIO_API_KEY": "test",
    "DATABASE_URL": "test",
}


@pytest.mark.parametrize("value,expected", [(None, 40), ("12", 12), ("500", 500)])
def test_max_tool_calls_configuration(value, expected):
    env = dict(BASE_ENV)
    if value is not None:
        env["MAX_TOOL_CALLS"] = value
    with patch.dict(os.environ, env, clear=True):
        assert Config.load().max_tool_calls == expected


@pytest.mark.parametrize("value", ["0", "501", "many"])
def test_invalid_max_tool_calls_fails_at_startup(value):
    from bot.models import ConfigError

    with patch.dict(os.environ, BASE_ENV | {"MAX_TOOL_CALLS": value}, clear=True):
        with pytest.raises(ConfigError):
            Config.load()
