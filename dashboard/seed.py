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

    bootstrap_email = bootstrap_email.strip()
    if not bootstrap_email:
        return

    async with SessionLocal() as db, db.begin():
        # Check if any admin already exists
        result = await db.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        existing_admin = result.scalar_one_or_none()
        if existing_admin is not None:
            logger.info("Bootstrap admin already exists")
            return

        result = await db.execute(
            select(User).where(User.uw_email == bootstrap_email)
        )
        admin = result.scalar_one_or_none()
        if admin is None:
            admin = User(
                uw_email=bootstrap_email,
                display_name=bootstrap_email.split("@")[0],
                role="admin",
            )
            db.add(admin)
        else:
            admin.role = "admin"

    logger.info("Bootstrap admin seeded: %s", bootstrap_email)
