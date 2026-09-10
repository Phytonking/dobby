import json

import pytest

from bot.contacts import Contacts, mask, parse_pairs, valid_email


def test_save_lookup_remove_round_trip(tmp_path):
    store = Contacts(tmp_path / "nested" / "contacts.json")
    assert store.lookup(["Maya"]) == ({}, ["Maya"])
    assert store.save("  Maya   Chen ", "<Maya.C@Example.com>", added_by=7) == "Maya Chen"
    found, missing = store.lookup(["maya chen", "Maya", "leo@y.org", "Bob", "Bob"])
    assert found == {
        "maya chen": "maya.c@example.com",
        "Maya": "maya.c@example.com",
        "leo@y.org": "leo@y.org",
    }
    assert missing == ["Bob"]
    saved = json.loads((tmp_path / "nested" / "contacts.json").read_text(encoding="utf-8"))
    assert saved["contacts"]["maya chen"]["added_by"] == 7
    assert store.all() == {"Maya Chen": "maya.c@example.com"}
    assert store.remove("MAYA CHEN") and not store.remove("Maya Chen")
    assert store.all() == {}
    assert not list(tmp_path.glob("**/*.tmp"))


def test_corrupt_or_missing_file_reads_as_empty(tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text("{not json", encoding="utf-8")
    assert Contacts(path).all() == {}
    path.write_text('{"contacts": []}', encoding="utf-8")
    assert Contacts(path).all() == {}


@pytest.mark.parametrize("bad", ["", "nope", "a@b", "<@123>", "two@x.com y@z.com", "a b@c.com"])
def test_invalid_emails_are_rejected(tmp_path, bad):
    assert not valid_email(bad)
    with pytest.raises(ValueError):
        Contacts(tmp_path / "c.json").save("Maya", bad)


def test_parse_pairs_handles_common_reply_shapes():
    assert parse_pairs("Maya: maya@x.com, Leonard is leo@y.org", ["Maya", "Leonard"]) == {
        "Maya": "maya@x.com",
        "Leonard": "leo@y.org",
    }
    assert parse_pairs("maya@x.com", ["Maya"]) == {"Maya": "maya@x.com"}
    assert parse_pairs("maya@x.com", ["Maya", "Leonard"]) == {}
    assert parse_pairs("maya - <maya@x.com>", ["Maya Chen"]) == {"Maya Chen": "maya@x.com"}
    assert parse_pairs("nothing here", ["Maya"]) == {}


def test_mask_hides_the_local_part():
    assert mask("maya@example.com") == "m***@example.com"
    assert mask("broken") == "***"
