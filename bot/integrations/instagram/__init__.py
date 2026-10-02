"""Instagram: publishing posts and stories from the group's account, always behind Confirm.

The model gets local tools only (list our posts, draft a post, draft a story); the Graph API calls
that actually publish run inside PendingActions after the requester confirms.
"""

from ..base import Integration
from . import commands
from .publish import DRAFT_POST_TOOL, DRAFT_STORY_TOOL, LIST_POSTS_TOOL

PROMPT = (
    "Instagram: use list_instagram_posts to see our recent posts (numbered). To publish, call "
    "draft_instagram_post (needs a public image URL and caption) or draft_instagram_story (an "
    "image URL, or post_number to feature one of our posts). Both wait for the requester's "
    "confirmation; you cannot publish directly."
)

INTEGRATION = Integration(
    key="instagram",
    label="Instagram",
    app="instagram",
    actions=(),  # publishing is confirm-gated; reads go through the local list tool
    local_tools=(LIST_POSTS_TOOL, DRAFT_POST_TOOL, DRAFT_STORY_TOOL),
    prompt=PROMPT,
    register_commands=commands.register,
    help_lines=(
        "`/instagram posts` → our recent posts, numbered",
        "`/instagram post image_url:https://… caption:…` → preview, then Confirm",
        "`/instagram story post:2` → put post #2's photo on our story (or `image_url:` for a fresh one)",
        "`@Dobby put Tuesday's workshop post on our story`",
    ),
)
