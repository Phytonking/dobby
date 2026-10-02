"""Unit tests for dashboard/auth.py token and cookie helpers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from dashboard import auth


def test_token_round_trip():
    token = auth.make_token("user-123")
    assert auth.verify_token(token) == "user-123"


def test_tampered_token_rejected():
    token = auth.make_token("user-123")
    tampered = "x" + token[1:]
    assert auth.verify_token(tampered) is None


def test_garbage_token_rejected():
    assert auth.verify_token("not-a-token") is None
    assert auth.verify_token("") is None


def test_tokens_are_unique_per_call():
    assert auth.make_token("u") != auth.make_token("u")


def test_session_expiry_is_seven_days_utc():
    expiry = auth.session_expiry()
    expected = datetime.now(timezone.utc) + timedelta(days=auth.SESSION_DAYS)
    assert abs((expiry - expected).total_seconds()) < 5


def test_cookie_flags_development():
    with patch.dict("os.environ", {"ENV": "development"}):
        assert auth._is_secure() is False


def test_cookie_flags_production():
    with patch.dict("os.environ", {"ENV": "production"}):
        assert auth._is_secure() is True


def test_set_session_cookie_is_httponly():
    calls = {}

    class FakeResponse:
        def set_cookie(self, **kwargs):
            calls.update(kwargs)

    auth.set_session_cookie(FakeResponse(), "tok")
    assert calls["httponly"] is True
    assert calls["key"] == auth.COOKIE_NAME
    assert calls["value"] == "tok"
