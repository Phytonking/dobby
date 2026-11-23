import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from .models import ConfigError
from .AIModels import ModelSettings


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
    composio_key: str
    model: str
    timezone: str
    mention_channels: frozenset[int] = frozenset()
    # The one Composio entity that owns Dobby's service accounts; shared with the dashboard.
    composio_entity: str = "dobby"
    # How many recent human messages Dobby reads from the channel before each request.
    context_limit: int = 50
    # Instagram Business/Creator account ID the connected Meta app manages (needed to publish).
    instagram_user_id: str = ""
    # Optional Notion page under which /notion note creates pages.
    notion_parent_page_id: str = ""
    model_backup: str = "gemini-3.1-flash-lite"
    ai_settings: ModelSettings | None = None
    # The agent loop's only backstop against a model that keeps calling tools.
    max_tool_calls: int = 40
    # Channel where Dobby opens a thread per incoming Instagram DM; 0 turns the relay off.
    instagram_dm_channel: int = 0
    # How often the relay asks Instagram for new DMs.
    instagram_dm_poll_seconds: int = 60

    @classmethod
    def load(cls):
        # Direct runs load .env; Compose supplies settings through the process environment.
        load_dotenv(os.getenv("DOBBY_ENV_FILE", ".env"), override=False, interpolate=False)
        required = ["DISCORD_TOKEN", "DISCORD_GUILD_ID", "COMPOSIO_API_KEY", "DATABASE_URL"]
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
        try:
            context_limit = int(os.getenv("CONTEXT_MESSAGE_LIMIT", "50"))
        except ValueError:
            context_limit = -1
        if not 0 <= context_limit <= 500:
            raise ConfigError("CONTEXT_MESSAGE_LIMIT must be a whole number from 0 to 500.")
        try:
            max_tool_calls = int(os.getenv("MAX_TOOL_CALLS", "40"))
        except ValueError:
            max_tool_calls = 0
        if not 1 <= max_tool_calls <= 500:
            raise ConfigError("MAX_TOOL_CALLS must be a whole number from 1 to 500.")
        try:
            dm_channel = int(os.getenv("INSTAGRAM_DM_CHANNEL_ID", "").strip() or 0)
        except ValueError:
            raise ConfigError("INSTAGRAM_DM_CHANNEL_ID must be a numeric Discord channel ID.") from None
        try:
            dm_poll = int(os.getenv("INSTAGRAM_DM_POLL_SECONDS", "").strip() or 60)
        except ValueError:
            dm_poll = 0
        if not 15 <= dm_poll <= 3600:
            raise ConfigError("INSTAGRAM_DM_POLL_SECONDS must be a whole number from 15 to 3600.")
        instagram_user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
        if dm_channel and not instagram_user_id:
            # Without our own account ID the relay cannot tell our replies from theirs.
            raise ConfigError("INSTAGRAM_DM_CHANNEL_ID needs INSTAGRAM_USER_ID to be set as well.")
        ai_settings = ModelSettings.from_env()
        return cls(
            os.environ["DISCORD_TOKEN"],
            guild,
            users,
            roles,
            channels,
            os.getenv("GEMINI_API_KEY", ""),
            os.environ["COMPOSIO_API_KEY"],
            ai_settings.primary.model,
            zone,
            mentions,
            os.getenv("COMPOSIO_ENTITY_ID", "dobby").strip() or "dobby",
            context_limit,
            instagram_user_id,
            os.getenv("NOTION_PARENT_PAGE_ID", "").strip(),
            ai_settings.backup.model if ai_settings.backup else "",
            ai_settings,
            max_tool_calls,
            dm_channel,
            dm_poll,
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
