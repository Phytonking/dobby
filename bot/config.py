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
    modal_base_url: str
    modal_token: str
    composio_key: str
    composio_entity_id: str
    model: str
    timezone: str
    mention_channels: frozenset[int] = frozenset()
    context_limit: int = 12
    reasoning_effort: str | None = None
    max_tool_calls: int = 40
    max_completion_tokens: int = 8192

    @classmethod
    def load(cls):
        # Compose mounts this file read-only; credential values never enter image metadata.
        load_dotenv(os.getenv("DOBBY_ENV_FILE", ".env"), override=False, interpolate=False)
        required = [
            "DISCORD_TOKEN",
            "DISCORD_GUILD_ID",
            "MODAL_BASE_URL",
            "MODAL_PROXY_TOKEN_ID",
            "MODAL_PROXY_TOKEN_SECRET",
            "MODAL_MODEL",
            "COMPOSIO_API_KEY",
            "DATABASE_URL",
        ]
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
        context_limit = int(os.getenv("CONTEXT_MESSAGE_LIMIT", "12"))
        # No per-request timeout wraps the agent loop, so this is the only
        # backstop against a confused model looping forever — real cost on
        # both the model endpoint and any destructive tool it keeps calling.
        # Generous by default, not unbounded; raise via env if you need more.
        max_tool_calls = int(os.getenv("MAX_TOOL_CALLS", "40"))
        max_completion_tokens = int(os.getenv("MODEL_MAX_TOKENS", "8192"))
        # The curl example's endpoint URL ends in /chat/completions; the openai
        # SDK's base_url wants the prefix up to /v1 and appends that itself.
        base_url = os.environ["MODAL_BASE_URL"].rstrip("/").removesuffix("/chat/completions")
        token = f"{os.environ['MODAL_PROXY_TOKEN_ID']}.{os.environ['MODAL_PROXY_TOKEN_SECRET']}"
        return cls(
            os.environ["DISCORD_TOKEN"],
            guild,
            users,
            roles,
            channels,
            base_url,
            token,
            os.environ["COMPOSIO_API_KEY"],
            os.getenv("COMPOSIO_ENTITY_ID", "dobby"),
            os.environ["MODAL_MODEL"],
            zone,
            mentions,
            context_limit,
            os.getenv("MODAL_REASONING_EFFORT") or None,
            max_tool_calls,
            max_completion_tokens,
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
