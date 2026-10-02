import pytest

from bot.models import mask, valid_email


@pytest.mark.parametrize("bad", ["", "nope", "a@b", "<@123>", "two@x.com y@z.com", "a b@c.com", None])
def test_invalid_emails_are_rejected(bad):
    assert not valid_email(bad)


@pytest.mark.parametrize("good", ["maya@uw.edu", "first.last+tag@example.co.uk"])
def test_valid_emails_are_accepted(good):
    assert valid_email(good)


def test_mask_hides_the_local_part():
    assert mask("maya@example.com") == "m***@example.com"
    assert mask("broken") == "***"
