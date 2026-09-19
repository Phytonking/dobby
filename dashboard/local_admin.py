"""CLI to set a local password for a dashboard user.

Usage (inside the container):
    python -m dashboard.local_admin <username_or_email>

Prompts for a password twice. Updates the user's password_hash in the DB.
"""

import asyncio
import getpass
import sys

import bcrypt
from sqlalchemy import select, update

from .database import SessionLocal
from .models import User


async def set_password(username: str) -> None:
    async with SessionLocal() as db:
        result = await db.execute(
            select(User).where(
                (User.uw_email == username) | (User.display_name == username)
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            print(f"No user found for '{username}'")
            sys.exit(1)

        pw = getpass.getpass("Password: ")
        pw2 = getpass.getpass("Confirm: ")
        if pw != pw2:
            print("Passwords do not match")
            sys.exit(1)
        if len(pw) < 8:
            print("Password must be at least 8 characters")
            sys.exit(1)

        hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        await db.execute(
            update(User).where(User.id == user.id).values(password_hash=hashed)
        )
        await db.commit()
        print(f"Password set for {user.display_name} ({user.uw_email})")
        print(f"\nLog in at: POST /auth/local  {{username: '{username}', password: '...'}}")
        print(f"Or use the login form at your dashboard URL.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m dashboard.local_admin <username_or_email>")
        sys.exit(1)
    asyncio.run(set_password(sys.argv[1]))
