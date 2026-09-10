import random
from unittest.mock import patch

import pytest

from bot import voice
from bot.voice import FALLBACK, FIELDS, RESPONSE_DIR, load_lines, placeholders, say

KEYS = sorted(path.stem for path in RESPONSE_DIR.glob("*.txt"))


def test_every_key_has_a_fallback_and_every_fallback_has_a_file():
    assert set(KEYS) == set(FALLBACK)


@pytest.mark.parametrize("key", KEYS)
def test_each_pool_has_twenty_distinct_usable_lines(key):
    lines = load_lines(key)
    assert len(lines) == 20
    assert len(set(lines)) == 20
    allowed = FIELDS.get(key, set())
    for line in lines:
        assert placeholders(line) <= allowed
        fields = {name: "x" for name in allowed}
        assert line.format(**fields)
    assert placeholders(FALLBACK[key]) <= allowed


def test_preview_outro_keeps_the_facts_users_rely_on():
    for line in load_lines("preview_outro"):
        assert "2 minutes" in line
        assert "UTC offset" in line


def test_say_formats_fields_and_reads_from_disk_each_time():
    with patch("bot.voice.load_lines", return_value=["Hi {question}"]) as load:
        assert say("needs_help", question="one") == "Hi one"
        assert say("needs_help", question="two") == "Hi two"
    assert load.call_count == 2


def test_unknown_key_or_bad_template_falls_back_without_raising(caplog):
    assert say("no_such_key") == "Dobby is at your service."
    assert "voice_missing" in caplog.text
    with patch("bot.voice.load_lines", return_value=["{missing}"]):
        assert say("needs_help", question="q") == FALLBACK["needs_help"].format(question="q")
    assert "voice_format_failed" in caplog.text


def test_say_varies_between_calls():
    with patch("bot.voice.pick", side_effect=random.Random(1).choice):
        assert len({say("created") for _ in range(40)}) > 1


def test_lines_with_unknown_placeholders_are_skipped(tmp_path):
    (tmp_path / "created.txt").write_text("ok\n# comment\n\n{bogus} no\n", encoding="utf-8")
    with patch.object(voice, "RESPONSE_DIR", tmp_path):
        assert load_lines("created") == ["ok"]
