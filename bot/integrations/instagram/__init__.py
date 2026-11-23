"""Instagram: publishing posts and stories from the group's account, always behind Confirm.

Incoming DMs are relayed into Discord threads by `relay.py` and answered from there, also behind Confirm.

The model gets local tools only (list our posts, draft a post, draft a story); the Graph API calls
that actually publish run inside PendingActions after the requester confirms.
"""

from ..base import Integration
from . import commands
from .publish import DRAFT_POST_TOOL, DRAFT_STORY_TOOL, LIST_POSTS_TOOL
from .relay import DRAFT_DM_REPLY_TOOL

PROMPT = (
    "Instagram: use list_instagram_posts to see our recent posts (numbered). To publish, call "
    "draft_instagram_post (needs a public image URL and caption) or draft_instagram_story (an "
    "image URL, or post_number to feature one of our posts). Both wait for the requester's "
    "confirmation; you cannot publish directly. Inside an Instagram DM thread Dobby opened, use "
    "draft_instagram_dm_reply with the exact words the requester wants sent back (ask if they have "
    "not said what to send; never invent a reply). It also waits for Confirm, then closes the thread."
)

INTEGRATION = Integration(
    key="instagram",
    label="Instagram",
    app="instagram",
    actions=(),  # publishing is confirm-gated; reads go through the local list tool
    local_tools=(LIST_POSTS_TOOL, DRAFT_POST_TOOL, DRAFT_STORY_TOOL, DRAFT_DM_REPLY_TOOL),
    prompt=PROMPT,
    register_commands=commands.register,
    help_lines=(
        "`/instagram posts` → our recent posts, numbered",
        "`/instagram post image_url:https://… caption:…` → preview, then Confirm",
        "`/instagram story post:2` → put post #2's photo on our story (or `image_url:` for a fresh one)",
        "`@Dobby put Tuesday's workshop post on our story`",
        "Instagram DMs open a thread in the DM channel: `/dm reply text:…` there → preview, Confirm "
        "sends it and closes the thread; `/dm close` closes it without replying",
    ),
)
