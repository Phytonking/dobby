from unittest.mock import Mock

from bot.lookup import clear_winner, find_events, score


def event(id, summary, start="2030-01-01T10:00:00Z", **extra):
    return {"id": id, "summary": summary, "start": {"dateTime": start}, "etag": '"v"', **extra}


def test_score_prefers_exact_then_substring_then_fuzzy():
    exact = score("Design review", "design review")
    contains = score("design review", "Q4 Design Review with vendors")
    fuzzy = score("design review", "Design reviews")
    unrelated = score("design review", "Lunch")
    assert exact == 1.0
    assert exact >= contains >= 0.9
    assert 0.5 <= fuzzy < 1.0
    assert unrelated < 0.5
    assert score("", "x") == 0.0 and score("x", None) == 0.0


def test_find_events_filters_uneditable_and_orders_best_first():
    calendar = Mock()
    calendar.list.return_value = [
        event("a", "Lunch"),
        event("b", "Design review", "2030-01-03T10:00:00Z"),
        event("c", "Design review", "2030-01-02T10:00:00Z"),
        event("d", "Design review", recurringEventId="parent"),
        event("e", "Design review", status="cancelled"),
        {"id": "f", "summary": "Design review", "start": {"date": "2030-01-01"}},
        event("g", "Design review sync"),
    ]
    matches = find_events(calendar, "design review", "UTC", days=30)
    assert [e["id"] for _, e in matches] == ["c", "b", "g"]
    window = calendar.list.call_args.args
    assert window[0] < window[1]


def test_clear_winner_requires_a_strong_unambiguous_top_match():
    assert clear_winner([]) is None
    only = (0.6, event("a", "x"))
    assert clear_winner([only]) is only[1]
    assert clear_winner([(1.0, event("a", "x")), (0.7, event("b", "y"))]) == event("a", "x")
    assert clear_winner([(1.0, event("a", "x")), (1.0, event("b", "x"))]) is None
    assert clear_winner([(0.7, event("a", "x")), (0.5, event("b", "y"))]) is None


def test_format_helpers_handle_missing_and_naive_values():
    from bot import format as fmt

    assert fmt.when(None, None, "UTC") == ""
    assert fmt.when("garbage", None, "UTC") == "garbage"
    assert fmt.when("2030-01-01T10:00:00+00:00", None, "UTC") == "Tue Jan 1, 2030 · 10:00 AM (UTC+00:00)"
    assert fmt.when("2030-01-01T23:00:00+00:00", "2030-01-02T01:00:00+00:00", "UTC") == (
        "Tue Jan 1, 2030 11:00 PM → Wed Jan 2, 2030 1:00 AM (UTC+00:00)"
    )
    assert fmt.changes({"summary": "A"}, {"summary": "A"}, "UTC") == []
    assert fmt.changes(None, {"location": "Room"}, "UTC") == ["Location: (none) → Room"]
