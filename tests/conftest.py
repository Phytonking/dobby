from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def deterministic_voice():
    """Pick the first phrasing of every response pool so assertions on wording are stable."""
    with patch("bot.voice.pick", side_effect=lambda lines: lines[0]):
        yield
