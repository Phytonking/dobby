"""LinkedIn: publishing posts from the group's account, always behind Confirm."""

from ..base import Integration
from . import commands
from .publish import DRAFT_TOOL

PROMPT = (
    "LinkedIn: to publish, call draft_linkedin_post with the final text; it is shown to the "
    "requester for confirmation and you cannot publish directly. Keep posts under 1,300 characters."
)

INTEGRATION = Integration(
    key="linkedin",
    label="LinkedIn",
    app="linkedin",
    actions=(),  # The model never posts directly; drafts go through the confirm gate
    local_tools=(DRAFT_TOOL,),
    prompt=PROMPT,
    register_commands=commands.register,
    help_lines=(
        "`/linkedin post text:We shipped v2 today…` → preview, then Confirm to publish",
        "`@Dobby draft a LinkedIn post about Friday's demo` → Dobby writes it from the chat, you confirm",
    ),
)
