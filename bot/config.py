import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


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
    context_limit: int = 6

    @classmethod
    def load(cls):
        load_dotenv()
        required = ["DISCORD_TOKEN", "DISCORD_GUILD_ID", "GEMINI_API_KEY"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise ValueError("Missing configuration: " + ", ".join(missing))
        users, roles = ids("ALLOWED_USER_IDS"), ids("ALLOWED_ROLE_IDS")
        if not users and not roles:
            raise ValueError("Set ALLOWED_USER_IDS or ALLOWED_ROLE_IDS; access defaults to denied.")
        zone = os.getenv("TEAM_TIMEZONE", "America/Denver")
        ZoneInfo(zone)
        return cls(
            os.environ["DISCORD_TOKEN"],
            int(os.environ["DISCORD_GUILD_ID"]),
            users,
            roles,
            ids("ALLOWED_CHANNEL_IDS"),
            os.environ["GEMINI_API_KEY"],
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            os.getenv("GOOGLE_TOKEN_FILE", "secrets/google-token.json"),
            os.getenv("GOOGLE_CALENDAR_ID", "primary"),
            zone,
            ids("MENTION_CHANNEL_IDS"),
            6,
        )

    def allows(self, guild, user, roles, channel):
        return (
            guild == self.guild
            and (not self.channels or channel in self.channels)
            and (user in self.users or bool(self.roles.intersection(roles)))
        )
