"""Instagram Graph API via Composio: list our media, and build posts/stories as PendingActions.

Publishing is two steps on the Graph API — create a media container, then publish it — and the
account is identified by INSTAGRAM_USER_ID. A story "featuring" one of our posts re-publishes that
post's image or video as a story; the API cannot attach the interactive share sticker.
"""

from ipaddress import ip_address
from urllib.parse import urlsplit

from bot.AIModels import FunctionDeclaration

from ...composio import find_key, run_action
from ...models import UserError
from ..base import LocalTool, PendingAction, RunContext

# Composio action names — verify against the catalog at deploy time (docs/BOT_ARCHITECTURE.md).
LIST_MEDIA = "INSTAGRAM_GET_USER_MEDIA"
CREATE_CONTAINER = "INSTAGRAM_CREATE_MEDIA_CONTAINER"
PUBLISH = "INSTAGRAM_PUBLISH_MEDIA"
MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"
MAX_POSTS = 10
MAX_CAPTION = 2200


def validate_media_url(value: str) -> None:
    """Require an HTTPS media link without credentials or a local IP literal."""
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError
        if any(c.isspace() for c in value) or url.hostname.lower() == "localhost":
            raise ValueError
        # Accessing .port also rejects malformed/out-of-range port numbers.
        if url.port not in (None, 443):
            raise ValueError
        try:
            address = ip_address(url.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
    except ValueError:
        raise UserError("image_url must be a public https URL without credentials") from None


def account_id(config) -> str:
    if not getattr(config, "instagram_user_id", ""):
        raise UserError("INSTAGRAM_USER_ID is not set, so Dobby cannot use Instagram yet.")
    return config.instagram_user_id


async def list_posts(toolset, config, entity_id: str, limit: int = MAX_POSTS) -> list[dict]:
    """Our most recent posts, numbered from 1 (newest)."""
    result = await run_action(
        toolset,
        LIST_MEDIA,
        {"ig_user_id": account_id(config), "fields": MEDIA_FIELDS, "limit": limit},
        entity_id,
    )
    if not result.get("success"):
        raise UserError(f"Instagram did not answer: {result.get('error', 'unknown error')}")
    items = find_key(result.get("data"), "data", "media", "items") or []
    posts = []
    for n, item in enumerate(items[:limit], start=1):
        if not isinstance(item, dict):
            continue
        posts.append(
            {
                "number": n,
                "id": item.get("id"),
                "caption": (item.get("caption") or "")[:200],
                "media_type": item.get("media_type"),
                "media_url": item.get("media_url") or item.get("thumbnail_url"),
                "permalink": item.get("permalink"),
                "timestamp": item.get("timestamp"),
            }
        )
    return posts


def format_posts(posts: list[dict]) -> str:
    if not posts:
        return "No posts found."
    return "\n".join(
        f"{p['number']}. {p['caption'] or '(no caption)'} — {p['permalink'] or p['id']}" for p in posts
    )


def _publish(toolset, config, entity_id: str, container_params: dict):
    async def execute() -> dict:
        ig_user_id = account_id(config)
        created = await run_action(
            toolset, CREATE_CONTAINER, {"ig_user_id": ig_user_id, **container_params}, entity_id
        )
        if not created.get("success"):
            return created
        creation_id = find_key(created.get("data"), "id", "creation_id", "container_id")
        if not creation_id:
            return {"success": False, "error": "Instagram did not return a media container ID"}
        return await run_action(
            toolset, PUBLISH, {"ig_user_id": ig_user_id, "creation_id": creation_id}, entity_id
        )

    return execute


def draft_post(toolset, config, entity_id: str, image_url: str, caption: str) -> PendingAction:
    validate_media_url(image_url)
    account_id(config)  # fail early with a clear message
    caption = caption.strip()[:MAX_CAPTION]
    return PendingAction(
        integration="instagram",
        label="Instagram post",
        preview=f"{caption}\n{image_url}",
        execute=_publish(toolset, config, entity_id, {"image_url": image_url, "caption": caption}),
    )


def draft_story(
    toolset, config, entity_id: str, media_url: str, *, featuring: dict | None = None
) -> PendingAction:
    validate_media_url(media_url)
    account_id(config)
    params = {"media_type": "STORIES"}
    if featuring and str(featuring.get("media_type", "")).upper() == "VIDEO":
        params["video_url"] = media_url
    else:
        params["image_url"] = media_url
    preview = media_url
    if featuring:
        preview = (
            f"Featuring post #{featuring['number']}: {featuring['caption'] or '(no caption)'}\n{media_url}"
        )
    return PendingAction(
        integration="instagram",
        label="Instagram story",
        preview=preview,
        execute=_publish(toolset, config, entity_id, params),
    )


async def story_from_post(toolset, config, entity_id: str, number: int) -> PendingAction:
    posts = await list_posts(toolset, config, entity_id)
    match = next((p for p in posts if p["number"] == number), None)
    if match is None:
        raise UserError(f"There is no post #{number}; /instagram posts lists 1–{len(posts)}.")
    if not match["media_url"]:
        raise UserError(f"Post #{number} has no media Dobby can reuse (carousels are not supported).")
    return draft_story(toolset, config, entity_id, match["media_url"], featuring=match)


# ----------------------------------------------------------------------------- local tools


async def list_tool(ctx: RunContext, params: dict) -> dict:
    try:
        posts = await list_posts(ctx.toolset, ctx.config, ctx.config.composio_entity)
    except UserError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "posts": posts}


async def draft_post_tool(ctx: RunContext, params: dict) -> dict:
    image_url, caption = str(params.get("image_url", "")).strip(), str(params.get("caption", "")).strip()
    if not image_url.startswith("http"):
        return {"success": False, "error": "image_url must be a public https URL"}
    try:
        return ctx.queue(draft_post(ctx.toolset, ctx.config, ctx.config.composio_entity, image_url, caption))
    except UserError as exc:
        return {"success": False, "error": str(exc)}


async def draft_story_tool(ctx: RunContext, params: dict) -> dict:
    try:
        if params.get("post_number") is not None:
            pending = await story_from_post(
                ctx.toolset, ctx.config, ctx.config.composio_entity, int(params["post_number"])
            )
        else:
            image_url = str(params.get("image_url", "")).strip()
            if not image_url.startswith("http"):
                return {"success": False, "error": "give either post_number or a public https image_url"}
            pending = draft_story(ctx.toolset, ctx.config, ctx.config.composio_entity, image_url)
    except (UserError, ValueError) as exc:
        return {"success": False, "error": str(exc)}
    return ctx.queue(pending)


LIST_POSTS_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="list_instagram_posts",
        description="Our account's recent Instagram posts, numbered from 1 (newest), with captions and links.",
        parameters={"type": "object", "properties": {}},
    ),
    handler=list_tool,
)

DRAFT_POST_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="draft_instagram_post",
        description="Queue an Instagram photo post for the requester to confirm. Nothing is published until they press Confirm.",
        parameters={
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "Public https URL of the image"},
                "caption": {"type": "string", "description": "The caption"},
            },
            "required": ["image_url", "caption"],
        },
    ),
    handler=draft_post_tool,
)

DRAFT_STORY_TOOL = LocalTool(
    declaration=FunctionDeclaration(
        name="draft_instagram_story",
        description=(
            "Queue an Instagram story for the requester to confirm. Give post_number (from "
            "list_instagram_posts) to feature one of our posts, or image_url for a fresh image."
        ),
        parameters={
            "type": "object",
            "properties": {
                "post_number": {"type": "integer", "description": "Which of our posts to feature"},
                "image_url": {"type": "string", "description": "Public https URL of an image"},
            },
        },
    ),
    handler=draft_story_tool,
)
