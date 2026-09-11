import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from bot.config import Config
from bot.main import check_data_dir, check_token_file
from bot.models import ConfigError
from scripts.link_google import main


def test_mounted_env_preserves_literal_secret_and_runtime_token_override(tmp_path):
    config_file = tmp_path / "dobby.env"
    config_file.write_text(
        "DISCORD_TOKEN=test-discord\nDISCORD_GUILD_ID=123\nALLOWED_USER_IDS=456\n"
        "GEMINI_API_KEY=literal-${MISSING_VARIABLE}\nGOOGLE_TOKEN_FILE=host-path.json\n"
        "TEAM_TIMEZONE=UTC\n",
        encoding="utf-8",
    )
    with patch.dict(
        os.environ,
        {"DOBBY_ENV_FILE": str(config_file), "GOOGLE_TOKEN_FILE": "/run/secrets/google-token.json"},
        clear=True,
    ):
        config = Config.load()
    assert config.token == "test-discord"
    assert config.gemini_key == "literal-${MISSING_VARIABLE}"
    assert config.token_file == "/run/secrets/google-token.json"
    assert config.allows(123, 456, [], 789)


def test_docker_oauth_uses_localhost_redirect_and_container_listener(tmp_path, capsys):
    token_file = tmp_path / "token.json"
    flow = Mock()
    flow.run_local_server.return_value.refresh_token = "fixture-refresh"
    flow.run_local_server.return_value.to_json.return_value = '{"test": true}'
    with (
        patch("scripts.link_google.InstalledAppFlow.from_client_secrets_file", return_value=flow),
        patch(
            "sys.argv",
            [
                "link_google.py",
                "--no-browser",
                "--port",
                "8765",
                "--bind",
                "0.0.0.0",
                "--output",
                str(token_file),
            ],
        ),
    ):
        main()
    options = flow.run_local_server.call_args.kwargs
    assert options["host"] == "localhost"
    assert options["bind_addr"] == "0.0.0.0"
    assert options["port"] == 8765
    assert options["open_browser"] is False
    assert options["timeout_seconds"] == 300
    assert token_file.read_text() == '{"test": true}'
    assert "fixture-refresh" not in capsys.readouterr().out


def test_config_errors_name_the_setting_to_fix(tmp_path):
    """Startup problems must be actionable; these messages carry no credential values."""
    base = "DISCORD_TOKEN=t\nDISCORD_GUILD_ID=123\nGEMINI_API_KEY=k\nALLOWED_USER_IDS=1\nTEAM_TIMEZONE=UTC\n"
    cases = [
        (base.replace("DISCORD_TOKEN=t\n", ""), "Missing configuration: DISCORD_TOKEN"),
        (base.replace("DISCORD_GUILD_ID=123", "DISCORD_GUILD_ID=abc"), "must be the numeric guild ID"),
        (base.replace("ALLOWED_USER_IDS=1\n", ""), "access defaults to denied"),
        (base.replace("TEAM_TIMEZONE=UTC", "TEAM_TIMEZONE=Mars/Olympus"), "not a known IANA zone"),
    ]
    for index, (text, expected) in enumerate(cases):
        config_file = tmp_path / f"case{index}.env"
        config_file.write_text(text, encoding="utf-8")
        with patch.dict(os.environ, {"DOBBY_ENV_FILE": str(config_file)}, clear=True):
            with pytest.raises(ConfigError) as caught:
                Config.load()
        assert expected in str(caught.value)


def test_token_file_problems_are_reported_before_discord_login(tmp_path):
    directory, absent = tmp_path / "as-dir", tmp_path / "absent.json"
    directory.mkdir()
    unparsable, partial = tmp_path / "bad.json", tmp_path / "partial.json"
    unparsable.write_text("not json", encoding="utf-8")
    partial.write_text('{"client_id": "c"}', encoding="utf-8")
    complete = tmp_path / "good.json"
    complete.write_text('{"refresh_token":"r","client_id":"c","client_secret":"s"}', encoding="utf-8")

    for path, expected in [
        (directory, "is a directory, not a file"),
        (absent, "not found"),
        (unparsable, "not valid JSON"),
        (partial, "missing: refresh_token, client_secret"),
    ]:
        with pytest.raises(ConfigError) as caught:
            check_token_file(SimpleNamespace(token_file=str(path)))
        assert expected in str(caught.value)
        assert "refresh_token_value" not in str(caught.value)

    check_token_file(SimpleNamespace(token_file=str(complete)))


def test_data_dir_is_created_and_must_be_writable(tmp_path):
    check_data_dir(SimpleNamespace(data_dir=str(tmp_path / "data")))
    assert (tmp_path / "data").is_dir() and not list((tmp_path / "data").iterdir())
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="not writable"):
        check_data_dir(SimpleNamespace(data_dir=str(blocker)))
