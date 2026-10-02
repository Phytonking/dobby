import re


class UserError(Exception):
    pass


class ConfigError(Exception):
    """Startup misconfiguration. Messages are authored here, so they are safe to display."""


EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def valid_email(text):
    return bool(text) and len(text) <= 254 and EMAIL.match(text) is not None


def mask(email):
    """m***@example.com, so listings never print full addresses into a channel."""
    local, _, domain = email.partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"
