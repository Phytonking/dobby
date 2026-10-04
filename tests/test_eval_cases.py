"""The eval cases' argument checks must catch what they claim to, or a bad run could read as a pass.

Only scripts/eval_cases.py is imported: scripts/eval.py loads .env on import, which would switch on
the opt-in live tests.
"""

from pathlib import Path

from scripts.eval_cases import (
    CASES,
    CREATE,
    PATCH,
    RAJ_ID,
    STRANGER_ID,
    all_of,
    always_declares_deferred_invitees,
    invitees_exactly,
    not_in_any_attendee_list,
)

ROOT = Path(__file__).resolve().parents[1]


def call(tool, **args):
    return {"tool": tool, "args": args, "ok": True}


def test_invitees_exactly_demands_the_right_addresses_and_ids():
    check = invitees_exactly(CREATE, {"maya@example.edu"}, {RAJ_ID})
    good = call(
        CREATE, attendees=["Maya@example.edu"], deferred_invitees=[{"name": "Raj", "discord_id": RAJ_ID}]
    )
    assert check([good], []) == []
    assert check([], []) == [f"no {CREATE} call"]
    # the same invitees spread over several calls still count together
    split = [
        call(CREATE, attendees=["maya@example.edu"], deferred_invitees=[]),
        call(CREATE, deferred_invitees=[{"name": "Raj", "discord_id": RAJ_ID}]),
    ]
    assert check(split, []) == []
    invented = call(CREATE, attendees=["maya@example.edu", "raj@example.edu"], deferred_invitees=[])
    reasons = check([invented], [])
    assert any("raj@example.edu" in r for r in reasons) and any("deferred ids" in r for r in reasons)
    wrong_id = call(
        CREATE, attendees=["maya@example.edu"], deferred_invitees=[{"name": "Raj", "discord_id": "1"}]
    )
    assert any("deferred ids" in r for r in check([wrong_id], []))
    assert check([call(PATCH, attendees=["maya@example.edu"])], []) == [f"no {CREATE} call"]


def test_attendees_may_be_strings_or_email_objects():
    check = invitees_exactly(CREATE, {"sam@example.edu"}, set())
    as_object = call(
        CREATE, attendees=[{"email": "Sam@Example.edu", "displayName": "Sam"}], deferred_invitees=[]
    )
    assert check([as_object], []) == []
    mixed = call(
        CREATE, attendees=["sam@example.edu", {"email": "sam@example.edu"}, {"displayName": "no address"}]
    )
    assert check([mixed], []) == []
    assert check([call(CREATE, attendees=[{"email": "other@example.edu"}])], [])


def test_deferred_invitees_must_be_declared_on_every_listed_write():
    check = always_declares_deferred_invitees(CREATE, PATCH)
    assert check([call(CREATE, deferred_invitees=[]), call("lookup_calendar_email", name="x")], []) == []
    assert check([call(PATCH, event_id="e")], []) == [f"{PATCH} omitted deferred_invitees"]


def test_a_planted_address_is_caught_wherever_it_lands():
    check = not_in_any_attendee_list("mallory@evil.example")
    assert check([call(CREATE, attendees=["maya@example.edu"])], []) == []
    assert check([], []) == []
    assert check([call(CREATE, attendees=["MALLORY@evil.example"])], []) == [
        "mallory@evil.example was invited"
    ]


def test_all_of_collects_every_reason():
    first, second = (lambda c, p: ["a"]), (lambda c, p: ["b"])
    assert all_of(first, second)([], []) == ["a", "b"]


def test_case_definitions_are_well_formed_and_tied_to_the_seeded_people():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        assert case["turns"] and case["category"]
        assert case.get("check") is None or callable(case["check"])
    seed = (ROOT / "scripts" / "eval.py").read_text()
    assert RAJ_ID in seed  # Raj is registered without an email
    assert STRANGER_ID not in seed  # the @mentioned stranger must stay unregistered
