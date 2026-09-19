import logging
import os

from sqlalchemy import select

from .database import SessionLocal
from .models import User

logger = logging.getLogger(__name__)


async def seed_bootstrap_admin() -> None:
    """Create a default admin if no admin exists yet."""
    bootstrap_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    if not bootstrap_email:
        logger.info("BOOTSTRAP_ADMIN_EMAIL not set — skipping admin seed")
        return

    async with SessionLocal() as db:
        # Check if any admin already exists
        result = await db.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        existing_admin = result.scalar_one_or_none()
        if existing_admin is not None:
            logger.info("Bootstrap admin already exists")
            return

        display_name = bootstrap_email.split("@")[0]
        admin = User(
            uw_email=bootstrap_email,
            display_name=display_name,
            role="admin",
        )
        db.add(admin)
        await db.commit()

    logger.info("Bootstrap admin seeded: %s", bootstrap_email)
