import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from .models import ConfigError


def ids(name):
    return frozenset(int(x.strip()) for x in os.getenv(name, "").split(",") if x.strip())


@dataclass(frozen=True)
class Config:
    token: str
    guild: int
    users: frozenset[int]
    roles: frozenset[int]
    channels: frozenset[int]
    gemini_key: str
    model: str
    token_file: str
    calendar: str
    timezone: str
    mention_channels: frozenset[int] = frozenset()
    context_limit: int = 12

    @classmethod
    def load(cls):
        # Compose mounts this file read-only; credential values never enter image metadata.
        load_dotenv(os.getenv("DOBBY_ENV_FILE", ".env"), override=False, interpolate=False)
        required = ["DISCORD_TOKEN", "DISCORD_GUILD_ID", "GEMINI_API_KEY"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise ConfigError("Missing configuration: " + ", ".join(missing))
        try:
            guild = int(os.environ["DISCORD_GUILD_ID"])
        except ValueError:
            raise ConfigError("DISCORD_GUILD_ID must be the numeric guild ID.") from None
        try:
            users, roles = ids("ALLOWED_USER_IDS"), ids("ALLOWED_ROLE_IDS")
            channels, mentions = ids("ALLOWED_CHANNEL_IDS"), ids("MENTION_CHANNEL_IDS")
        except ValueError:
            raise ConfigError("ALLOWED_*/MENTION_CHANNEL_IDS must be comma-separated numeric IDs.") from None
        if not users and not roles:
            raise ConfigError("Set ALLOWED_USER_IDS or ALLOWED_ROLE_IDS; access defaults to denied.")
        zone = os.getenv("TEAM_TIMEZONE", "America/Denver")
        try:
            ZoneInfo(zone)
        except Exception:
            raise ConfigError(f"TEAM_TIMEZONE is not a known IANA zone: {zone}") from None
        return cls(
            os.environ["DISCORD_TOKEN"],
            guild,
            users,
            roles,
            channels,
            os.environ["GEMINI_API_KEY"],
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            os.getenv("GOOGLE_TOKEN_FILE", "secrets/google-token.json"),
            os.getenv("GOOGLE_CALENDAR_ID", "primary"),
            zone,
            mentions,
            12,
        )

    def mentionable(self, channel):
        """Empty MENTION_CHANNEL_IDS permits any channel, matching ALLOWED_CHANNEL_IDS."""
        return not self.mention_channels or channel in self.mention_channels

    def allows(self, guild, user, roles, channel):
        return (
            guild == self.guild
            and (not self.channels or channel in self.channels)
            and (user in self.users or bool(self.roles.intersection(roles)))
        )
